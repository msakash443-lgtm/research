import uuid

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.connectors.base import ConnectorBase, ConnectorError, PaperRecord, SearchPage, SearchRequest
from app.database import SessionLocal
from app.models import AuditEvent, Project, SearchQuery, User
from app.search_query import BooleanQuery, ConceptBlock
from app.search_runner import SearchError, SearchFailed, diff_runs, rerun_search, run_search

QUERY = BooleanQuery(blocks=(ConceptBlock(label="a", terms=("remote work", "telework")), ConceptBlock(label="b", terms=("productivity",))))


def rec(i, title=None, doi=None, year=2020, authors=("Ada Lovelace",)):
    return PaperRecord(
        connector="openalex",
        external_id=f"W{i}",
        title=title or f"A sufficiently long distinct title number {i} about work",
        doi=doi,
        year=year,
        authors=authors,
    )


class FakeConnector(ConnectorBase):
    name = "openalex"

    def __init__(self, records, page=2, total=None, fail_on_page=None):
        self.records, self.page, self.total, self.fail_on_page = list(records), page, total, fail_on_page
        self.requests: list[SearchRequest] = []

    def search(self, request: SearchRequest) -> SearchPage:
        self.requests.append(request)
        n = len(self.requests)
        if self.fail_on_page == n:
            raise ConnectorError("boom with secret-ish detail")
        start = int(request.cursor or 0)
        chunk = self.records[start : start + min(self.page, request.limit)]
        nxt = start + len(chunk)
        return SearchPage(records=tuple(chunk), total=self.total, next_cursor=str(nxt) if nxt < len(self.records) else None)


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setattr(get_settings(), "connectors_enabled", ["openalex"])


def setup():
    db = SessionLocal()
    user = User(email=f"{uuid.uuid4()}@example.org", display_name="O")
    db.add(user)
    db.flush()
    project = Project(owner_id=user.id, title="P")
    db.add(project)
    db.flush()
    return db, project


def test_run_records_query_filters_counts_and_results():
    db, project = setup()
    with db:
        conn = FakeConnector([rec(i) for i in range(5)], total=1234)
        row = run_search(db, project=project, actor="u1", connector=conn, query=QUERY, filters={"year_from": 2015})
        db.commit()
        assert row.version == 1 and row.database == "openalex"
        assert row.query_string == '("remote work" OR "telework") AND ("productivity")'
        assert row.filters == {"year_from": 2015}
        assert row.run_at is not None and row.created_by == "u1"
        assert row.n_results == 5
        assert row.counts == {
            "retrieved": 5, "repeated_ids": 0, "unique": 5, "duplicates_removed": 0,
            "reported_total": 1234, "truncated": False, "skipped_records": 0, "cache_bypassed": True,
        }
        assert [r["id"] for r in row.results] == [f"openalex:W{i}" for i in range(5)]
        assert row.exact is True and row.caveats == []
        assert len(conn.requests) == 3  # paged 2 + 2 + 1
        event = db.scalar(select(AuditEvent).where(AuditEvent.action == "search.run"))
        assert event.actor == "u1" and event.payload_json["counts"]["unique"] == 5
        assert "remote work" not in str(event.payload_json)


def test_duplicates_are_counted_and_removed():
    db, project = setup()
    with db:
        records = [
            rec(1, doi="10.1234/x"),
            rec(2, doi="10.1234/X"),  # same DOI as 1
            rec(3, title="Shared title long enough to be identifying", year=2019),
            rec(4, title="Shared Title long enough to be identifying", year=2019),  # same work key
            rec(5),
            rec(5),  # same id repeated across pages
        ]
        row = run_search(db, project=project, actor="u", connector=FakeConnector(records), query=QUERY)
        assert row.counts["retrieved"] == 6
        assert row.counts["repeated_ids"] == 1
        assert row.counts["unique"] == 3
        assert row.counts["duplicates_removed"] == 3
        assert row.n_results == 3
        assert row.results[0]["doi"] == "10.1234/x"


def test_result_cap_is_reported_as_truncated():
    db, project = setup()
    with db:
        conn = FakeConnector([rec(i) for i in range(10)], page=3)
        row = run_search(db, project=project, actor="u", connector=conn, query=QUERY, max_results=4)
        assert row.counts["retrieved"] == 4 and row.counts["truncated"] is True
        assert [r.limit for r in conn.requests] == [4, 1]


def test_no_truncation_flag_when_everything_fits():
    db, project = setup()
    with db:
        row = run_search(db, project=project, actor="u", connector=FakeConnector([rec(i) for i in range(4)], page=2), query=QUERY, max_results=4)
        assert row.counts["truncated"] is False and row.counts["retrieved"] == 4


def test_failure_saves_nothing_but_logs_and_raises():
    db, project = setup()
    with db:
        with pytest.raises(SearchFailed):
            run_search(db, project=project, actor="u", connector=FakeConnector([rec(i) for i in range(5)], fail_on_page=2), query=QUERY)
        db.commit()
        assert db.scalar(select(SearchQuery).where(SearchQuery.project_id == project.id)) is None
        event = db.scalar(select(AuditEvent).where(AuditEvent.action == "search.failed"))
        assert event.payload_json["error"] == "ConnectorError"
        assert "secret" not in str(event.payload_json)


def test_rerun_repeats_same_string_and_filters_as_next_version_and_diffs():
    db, project = setup()
    with db:
        first = run_search(db, project=project, actor="u", connector=FakeConnector([rec(1), rec(2)]), query=QUERY, filters={"year_from": 2010})
        again = FakeConnector([rec(2), rec(3)])
        second = rerun_search(db, project=project, actor="u2", connector=again, search_id=first.search_id)
        assert second.version == 2 and second.search_id == first.search_id
        assert again.requests[0].query == first.query_string
        assert again.requests[0].filters == {"year_from": 2010}
        assert second.created_by == "u2"
        assert diff_runs(first, second) == {"added": ["openalex:W3"], "removed": ["openalex:W1"]}
        third = rerun_search(db, project=project, actor="u", connector=FakeConnector([]), search_id=first.search_id)
        assert third.version == 3 and third.n_results == 0 and third.results == []


def test_rerun_refuses_unknown_search_other_project_or_other_database():
    db, project = setup()
    with db:
        first = run_search(db, project=project, actor="u", connector=FakeConnector([rec(1)]), query=QUERY)
        with pytest.raises(SearchError):
            rerun_search(db, project=project, actor="u", connector=FakeConnector([]), search_id=uuid.uuid4())
        other = Project(owner_id=project.owner_id, title="Other")
        db.add(other)
        db.flush()
        with pytest.raises(SearchError):  # another project's search is invisible
            rerun_search(db, project=other, actor="u", connector=FakeConnector([]), search_id=first.search_id)
        wrong = FakeConnector([])
        wrong.name = "crossref"
        with pytest.raises(SearchError):
            rerun_search(db, project=project, actor="u", connector=wrong, search_id=first.search_id)


def test_disabled_connector_and_bad_cap_are_refused_before_any_call(monkeypatch):
    db, project = setup()
    with db:
        conn = FakeConnector([rec(1)])
        monkeypatch.setattr(get_settings(), "connectors_enabled", [])
        with pytest.raises(SearchError):
            run_search(db, project=project, actor="u", connector=conn, query=QUERY)
        monkeypatch.setattr(get_settings(), "connectors_enabled", ["openalex"])
        for bad in (0, 2001):
            with pytest.raises(SearchError):
                run_search(db, project=project, actor="u", connector=conn, query=QUERY, max_results=bad)
        assert conn.requests == []


def test_inexact_adaptation_is_kept_with_the_record():
    db, project = setup()
    with db:
        wild = BooleanQuery(blocks=(ConceptBlock(label="a", terms=("employ*",)),))
        row = run_search(db, project=project, actor="u", connector=FakeConnector([rec(1)]), query=wild)
        assert row.exact is False and row.caveats
        again = rerun_search(db, project=project, actor="u", connector=FakeConnector([rec(1)]), search_id=row.search_id)
        assert again.exact is False and again.caveats == row.caveats
