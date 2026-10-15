"""Plan M2.13: citation counts on sources and search results, ranking, and the threshold filter.

A missing count is "unknown", never 0: unknown items are never hidden by the threshold; they stay
in the list, flagged, after every counted item, and the response says how many there are.
"""

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.connectors.base import PaperRecord, citation_count
from app.connectors.crossref import to_record as crossref_record
from app.connectors.ieee_xplore import to_record as ieee_record
from app.connectors.openalex import to_record as openalex_record
from app.connectors.scopus import to_record as scopus_record
from app.connectors.semantic_scholar import FIELDS, to_record as s2_record
from app.connectors.web_of_science import to_record as wos_record
from app.database import SessionLocal
from app.main import app
from app.models import SearchQuery, Source, utcnow
from app.search_runner import _summarise


# --- connectors -------------------------------------------------------------------


def test_each_connector_maps_its_citation_count_and_missing_means_unknown():
    work = {"id": "https://openalex.org/W1", "display_name": "An OpenAlex work"}
    assert openalex_record({**work, "cited_by_count": 12}).cited_by_count == 12
    assert openalex_record({**work, "cited_by_count": 0}).cited_by_count == 0
    assert openalex_record(work).cited_by_count is None

    paper = {"paperId": "a" * 40, "title": "An S2 paper"}
    assert s2_record({**paper, "citationCount": 7}).cited_by_count == 7
    assert s2_record(paper).cited_by_count is None
    assert "citationCount" in FIELDS.split(",")  # requested, or S2 never sends it

    item = {"DOI": "10.1000/xyz", "title": ["A Crossref work"]}
    assert crossref_record({**item, "is-referenced-by-count": 3}).cited_by_count == 3
    assert crossref_record(item).cited_by_count is None


def test_licensed_connectors_map_their_citation_count_and_missing_means_unknown():
    entry = {"dc:title": "A Scopus record", "dc:identifier": "SCOPUS_ID:85000000001"}
    assert scopus_record({**entry, "citedby-count": "12"}).cited_by_count == 12  # Scopus sends a string
    assert scopus_record({**entry, "citedby-count": "0"}).cited_by_count == 0
    assert scopus_record(entry).cited_by_count is None
    for bad in ("", "-1", "1.5", "12a", 12.0, None):
        assert scopus_record({**entry, "citedby-count": bad}).cited_by_count is None

    hit = {"uid": "WOS:000000000000001", "title": "A WoS record"}
    citations = [{"db": "BCI", "count": 40}, {"db": "WOS", "count": 9}]
    assert wos_record({**hit, "citations": citations}).cited_by_count == 9  # Core Collection only
    assert wos_record({**hit, "citations": [{"db": "BCI", "count": 40}]}).cited_by_count is None
    assert wos_record({**hit, "citations": [{"db": "WOS", "count": "9"}]}).cited_by_count is None
    assert wos_record({**hit, "citations": "9"}).cited_by_count is None
    assert wos_record(hit).cited_by_count is None

    article = {"article_number": "1234567", "title": "An IEEE article"}
    assert ieee_record({**article, "citing_paper_count": 5}).cited_by_count == 5
    assert ieee_record({**article, "citing_patent_count": 5}).cited_by_count is None  # patents are not papers
    assert ieee_record(article).cited_by_count is None


@pytest.mark.parametrize("bad", [-1, "12", 1.5, True, None, [3]])
def test_malformed_counts_are_unknown_not_zero(bad):
    assert citation_count(bad) is None
    assert openalex_record({"id": "https://openalex.org/W1", "display_name": "T", "cited_by_count": bad}).cited_by_count is None


def test_a_paper_record_refuses_a_negative_count():
    with pytest.raises(ValueError):
        PaperRecord(connector="x", external_id="1", title="T", cited_by_count=-1)


# --- search results ---------------------------------------------------------------


def _rec(n, count, **over):
    fields = {"connector": "openalex", "external_id": f"W{n}", "title": f"A distinct and long enough title number {n}",
              "year": 2000 + n, "authors": (f"Author{n} Family{n}",), "cited_by_count": count}
    fields.update(over)
    return PaperRecord(**fields)


def test_a_run_stores_each_results_count_and_merged_duplicates_keep_the_highest():
    duplicate = _rec(9, 50, external_id="W9b", doi="10.1000/dup")
    records = [_rec(1, 4), _rec(2, None), _rec(9, 20, doi="10.1000/dup"), duplicate]
    results, counts = _summarise(records)
    by_id = {r["id"]: r["cited_by_count"] for r in results}
    assert counts["unique"] == 3
    assert by_id["openalex:W1"] == 4 and by_id["openalex:W2"] is None and by_id["openalex:W9"] == 50


# --- API ----------------------------------------------------------------------------


@pytest.fixture
def world():
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": f"cites-{uuid.uuid4().hex}@example.com", "display_name": "C"})
    project = client.post("/api/projects", json={"title": "Citation project"}).json()["id"]
    return client, project


def _add_sources(project, counts):
    with SessionLocal() as db:
        for title, count in counts.items():
            db.add(Source(
                project_id=uuid.UUID(project), title=title, citation_count=count,
                citation_count_source="openalex" if count is not None else None,
                citation_count_at=datetime.now(timezone.utc) if count is not None else None,
            ))
        db.commit()


def test_sources_rank_by_count_and_the_threshold_hides_only_known_low_counts(world):
    client, project = world
    _add_sources(project, {"Low": 2, "High": 90, "Unknown": None, "Zero": 0, "Mid": 10})

    everything = client.get(f"/api/projects/{project}/sources/by-citations").json()
    assert [s["title"] for s in everything["sources"]] == ["High", "Mid", "Low", "Zero", "Unknown"]
    assert everything["min_citations"] is None and everything["threshold_from"] is None
    assert everything["hidden_below_threshold"] == 0 and everything["unknown_count"] == 1

    filtered = client.get(f"/api/projects/{project}/sources/by-citations", params={"min_citations": 5}).json()
    assert [(s["title"], s["citation_count_unknown"]) for s in filtered["sources"]] == [
        ("High", False), ("Mid", False), ("Unknown", True)
    ]
    assert filtered["sources"][-1]["citation_count"] is None  # unknown is not reported as 0
    assert filtered["sources"][0]["citation_count_source"] == "openalex"
    assert (filtered["total"], filtered["shown"], filtered["hidden_below_threshold"], filtered["unknown_count"]) == (5, 3, 2, 1)
    assert filtered["threshold_from"] == "request"


def test_the_brief_threshold_applies_by_default_and_a_request_overrides_it(world):
    client, project = world
    _add_sources(project, {"Low": 2, "High": 90})
    client.put(f"/api/projects/{project}/brief", json={"topic": "Topic", "min_citations": 5})

    by_brief = client.get(f"/api/projects/{project}/sources/by-citations").json()
    assert by_brief["min_citations"] == 5 and by_brief["threshold_from"] == "brief"
    assert [s["title"] for s in by_brief["sources"]] == ["High"]

    overridden = client.get(f"/api/projects/{project}/sources/by-citations", params={"min_citations": 0}).json()
    assert overridden["threshold_from"] == "request" and overridden["shown"] == 2


def test_a_person_cannot_set_a_citation_count(world):
    client, project = world
    created = client.post(f"/api/projects/{project}/sources", json={"title": "Typed in", "citation_count": 9999})
    assert created.status_code == 201
    assert created.json()["citation_count"] is None and created.json()["citation_count_source"] is None


def test_negative_thresholds_are_refused(world):
    client, project = world
    assert client.get(f"/api/projects/{project}/sources/by-citations", params={"min_citations": -1}).status_code == 422


def _add_run(project, version, results):
    with SessionLocal() as db:
        existing = db.query(SearchQuery).filter_by(project_id=uuid.UUID(project)).first()
        search_id = existing.search_id if existing else uuid.uuid4()
        db.add(SearchQuery(
            project_id=uuid.UUID(project), search_id=search_id, database="openalex", query_string='"x"',
            run_at=utcnow(), n_results=len(results), version=version, counts={"unique": len(results)}, results=results,
        ))
        db.commit()
    return search_id


def test_search_results_rank_filter_and_keep_unknowns_including_runs_saved_before_counts(world):
    client, project = world
    old = [{"id": "W1", "doi": None, "work_key": "a"}]  # a run saved before M2.13: no count key at all
    search_id = _add_run(project, 1, old)
    _add_run(project, 2, [
        {"id": "W1", "doi": None, "work_key": "a", "cited_by_count": 3},
        {"id": "W2", "doi": None, "work_key": "b", "cited_by_count": None},
        {"id": "W3", "doi": None, "work_key": "c", "cited_by_count": 40},
    ])

    latest = client.get(f"/api/projects/{project}/searches/{search_id}/results", params={"min_citations": 5}).json()
    assert latest["version"] == 2
    assert [(r["id"], r["citation_count_unknown"]) for r in latest["results"]] == [("W3", False), ("W2", True)]
    assert (latest["hidden_below_threshold"], latest["unknown_count"]) == (1, 1)

    first = client.get(f"/api/projects/{project}/searches/{search_id}/results", params={"version": 1}).json()
    assert first["results"] == [{**old[0], "cited_by_count": None, "citation_count_unknown": True}]
    assert first["unknown_count"] == 1


def test_an_unknown_search_or_version_is_404(world):
    client, project = world
    search_id = _add_run(project, 1, [])
    assert client.get(f"/api/projects/{project}/searches/{uuid.uuid4()}/results").status_code == 404
    assert client.get(f"/api/projects/{project}/searches/{search_id}/results", params={"version": 7}).status_code == 404
