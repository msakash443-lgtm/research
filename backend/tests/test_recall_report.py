import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.dedupe import DedupeRecord, work_key
from app.main import app
from app.models import SearchQuery
from app.recall_report import GoldPaper, report

_clock = [datetime(2026, 3, 1, tzinfo=timezone.utc)]
LONG = "Remote work and worker productivity evidence"


@pytest.fixture
def world():
    tag = uuid.uuid4().hex[:8]
    c = TestClient(app)
    c.post("/api/auth/development/login", json={"email": f"recall-{tag}@example.com", "display_name": "R"})
    pid = c.post("/api/projects", json={"title": "Recall"}).json()["id"]
    return c, pid


def seed(c, pid, **body):
    r = c.post(f"/api/projects/{pid}/seeds", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def entry(i, doi=None, title=None, year=None, authors=()):
    key = work_key(DedupeRecord(title=title, year=year, authors=list(authors))) if title else None
    return {"id": f"x:{i}", "doi": doi, "work_key": key}


def run(pid, results, database="openalex", truncated=False, search_id=None, version=1):
    _clock[0] += timedelta(minutes=1)
    with SessionLocal() as db:
        db.add(
            SearchQuery(
                project_id=uuid.UUID(pid), search_id=search_id or uuid.uuid4(), database=database, query_string="q",
                version=version, run_at=_clock[0], n_results=len(results), results=results,
                counts={"retrieved": len(results), "truncated": truncated},
            )
        )
        db.commit()


def get(c, pid, **params):
    r = c.get(f"/api/projects/{pid}/recall-report", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def codes(item):
    return [x["code"] for x in item["reasons"]]


def test_no_gold_set_and_not_searched_are_not_scored(world):
    c, pid = world
    data = get(c, pid)
    assert data["status"] == "no_gold_set" and data["recall"] is None and data["meets_target"] is None
    seed(c, pid, title=LONG, doi="10.1234/a")
    data = get(c, pid)
    assert data["status"] == "not_searched" and data["recall"] is None and data["meets_target"] is None
    assert data["missed_papers"] == [] and data["checked"] == 0


def test_target_is_95_percent_by_default(world):
    c, pid = world
    for i in range(20):
        seed(c, pid, title=f"Gold paper number {i}", doi=f"10.1234/g{i}")
    run(pid, [entry(i, doi=f"10.1234/g{i}") for i in range(19)])
    data = get(c, pid)
    assert data["target"] == 0.95 and data["recall"] == 0.95
    assert data["status"] == "meets_target" and data["meets_target"] is True
    assert [m["title"] for m in data["missed_papers"]] == ["Gold paper number 19"]


def test_below_target_lists_missed_papers_with_not_retrieved(world):
    c, pid = world
    for i in range(10):
        seed(c, pid, title=f"Gold paper number {i}", doi=f"10.1234/g{i}")
    run(pid, [entry(i, doi=f"10.1234/g{i}") for i in range(9)] + [entry(99, doi="10.1234/noise")])
    data = get(c, pid)
    assert data["recall"] == 0.9 and data["status"] == "below_target" and data["meets_target"] is False
    (missed,) = data["missed_papers"]
    assert missed["doi"] == "10.1234/g9" and codes(missed) == ["not_retrieved"]
    assert data["found"] == 9 and data["missed"] == 1 and data["checked"] == 10


def test_target_can_be_set_within_bounds(world):
    c, pid = world
    for i in range(10):
        seed(c, pid, title=f"Gold paper number {i}", doi=f"10.1234/g{i}")
    run(pid, [entry(i, doi=f"10.1234/g{i}") for i in range(9)])
    assert get(c, pid, target=0.9)["meets_target"] is True
    for bad in (0.3, 1.5, "x"):
        assert c.get(f"/api/projects/{pid}/recall-report", params={"target": bad}).status_code == 422


def test_same_title_with_another_doi_is_explained(world):
    c, pid = world
    seed(c, pid, title=LONG, doi="10.1234/published", year=2020)
    run(pid, [entry(1, doi="10.1234/preprint", title=LONG, year=2019)])
    (missed,) = get(c, pid)["missed_papers"]
    assert codes(missed) == ["doi_differs"]
    assert "10.1234/preprint" in missed["reasons"][0]["detail"]


def test_same_title_with_other_year_or_author_is_explained(world):
    c, pid = world
    seed(c, pid, title=LONG, year=2020)
    seed(c, pid, title="Another quite long title about teams", authors=["Grace Hopper"])
    run(
        pid,
        [
            entry(1, title=LONG, year=2021),
            entry(2, title="Another quite long title about teams", year=2018, authors=["Alan Turing"]),
        ],
        database="crossref",
    )
    missed = {m["title"]: m for m in get(c, pid)["missed_papers"]}
    year_item, author_item = missed[LONG], missed["Another quite long title about teams"]
    assert codes(year_item) == ["metadata_differs"] and "year 2021" in year_item["reasons"][0]["detail"]
    assert "crossref" in year_item["reasons"][0]["detail"]
    assert codes(author_item) == ["metadata_differs"] and "turing" in author_item["reasons"][0]["detail"]


def test_capped_search_is_given_as_a_possible_reason(world):
    c, pid = world
    seed(c, pid, title=LONG, doi="10.1234/a")
    run(pid, [entry(1, doi="10.1234/b")], truncated=True)
    (missed,) = get(c, pid)["missed_papers"]
    assert codes(missed) == ["result_cap", "not_retrieved"]


def test_uncheckable_gold_papers_are_listed_not_counted(world):
    c, pid = world
    seed(c, pid, title="Short")
    seed(c, pid, title=LONG, doi="10.1234/a")
    run(pid, [entry(1, doi="10.1234/a")])
    data = get(c, pid)
    assert data["recall"] == 1.0 and data["checked"] == 1 and data["gold_papers"] == 2
    assert [u["title"] for u in data["unchecked"]] == ["Short"]


def test_only_the_latest_version_and_this_project_count(world):
    c, pid = world
    seed(c, pid, title=LONG, doi="10.1234/a")
    sid = uuid.uuid4()
    run(pid, [entry(1, doi="10.1234/a")], search_id=sid, version=1)
    run(pid, [entry(2, doi="10.1234/z")], search_id=sid, version=2)
    other = c.post("/api/projects", json={"title": "Other"}).json()["id"]
    run(other, [entry(3, doi="10.1234/a")])
    data = get(c, pid)
    assert data["recall"] == 0.0 and len(data["searches"]) == 1 and data["searches"][0]["version"] == 2


def test_found_papers_list_which_searches_found_them(world):
    c, pid = world
    seed(c, pid, title=LONG, doi="10.1234/a")
    run(pid, [entry(1, doi="10.1234/zzz")], database="openalex")
    run(pid, [entry(2, doi="10.1234/a")], database="crossref")
    (found,) = get(c, pid)["found_papers"]
    assert len(found["found_in"]) == 1


def test_report_never_writes(world):
    c, pid = world
    seed(c, pid, title=LONG, doi="10.1234/a")
    run(pid, [])
    with SessionLocal() as db:
        before = db.query(SearchQuery).count()
    get(c, pid)
    with SessionLocal() as db:
        assert db.query(SearchQuery).count() == before


class _Run:
    def __init__(self, results, truncated=False):
        self.search_id, self.database, self.version = uuid.uuid4(), "openalex", 1
        self.counts, self.results = {"truncated": truncated}, results


def test_pure_report_on_gold_papers():
    gold = [GoldPaper(id="g1", title=LONG, doi="10.1234/a"), GoldPaper(id="g2", title="A second long gold title")]
    data = report(gold, [_Run([entry(1, doi="10.1234/a")])])
    assert data["recall"] == 0.5 and data["missed_papers"][0]["id"] == "g2"


def test_rounding_does_not_inflate_meets_target_at_boundary():
    """189 of 199 is ~94.975%, which rounds to 0.950 for display, but is below the 0.95 target."""
    gold = [GoldPaper(id=f"g{i}", title=f"Gold title number {i}", doi=f"10.1234/{i}") for i in range(199)]
    # First 189 entries found in run
    found_entries = [entry(i, doi=f"10.1234/{i}") for i in range(189)]
    data = report(gold, [_Run(found_entries)], target=0.95)
    assert data["checked"] == 199
    assert data["found"] == 189
    assert data["missed"] == 10
    assert data["recall"] == 0.95  # Display rounded to 3 decimal places
    assert data["meets_target"] is False
    assert data["status"] == "below_target"


def test_exact_target_match_meets_target():
    """95 of 100 is exactly 0.950, which meets the 0.95 target."""
    gold = [GoldPaper(id=f"g{i}", title=f"Gold title number {i}", doi=f"10.1234/{i}") for i in range(100)]
    found_entries = [entry(i, doi=f"10.1234/{i}") for i in range(95)]
    data = report(gold, [_Run(found_entries)], target=0.95)
    assert data["recall"] == 0.95
    assert data["meets_target"] is True
    assert data["status"] == "meets_target"


def test_x15_fixture_gold_set_can_be_reported():
    """Once the scholar's gold set (X.15) is filled in, its known-relevant papers feed `report` directly."""
    from tests.gold.loader import load_gold_set

    papers = [p for p in load_gold_set()["papers"] if p.get("known_relevant")]
    if not papers:
        pytest.skip("gold set not supplied yet (X.15 / Q10)")
    gold = [GoldPaper(id=p["id"], title=p["title"], doi=p.get("doi")) for p in papers]
    data = report(gold, [])
    assert data["status"] == "not_searched" and data["gold_papers"] == len(gold)
