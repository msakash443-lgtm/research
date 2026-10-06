import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.dedupe import DedupeRecord, work_key
from app.main import app
from app.models import SearchQuery

_clock = [datetime(2026, 2, 1, tzinfo=timezone.utc)]


@pytest.fixture
def world():
    tag = uuid.uuid4().hex[:8]
    c = TestClient(app)
    c.post("/api/auth/development/login", json={"email": f"seed-{tag}@example.com", "display_name": "S"})
    pid = c.post("/api/projects", json={"title": "Seeds"}).json()["id"]
    return c, pid


def seed(c, pid, **body):
    r = c.post(f"/api/projects/{pid}/seeds", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def entry(i, doi=None, title=None, year=None, authors=()):
    key = work_key(DedupeRecord(title=title, year=year, authors=list(authors))) if title else None
    return {"id": f"x:{i}", "doi": doi, "work_key": key}


def run(pid, results, database="openalex", truncated=False):
    _clock[0] += timedelta(minutes=1)
    with SessionLocal() as db:
        db.add(
            SearchQuery(
                project_id=uuid.UUID(pid), search_id=uuid.uuid4(), database=database, query_string="q", version=1,
                run_at=_clock[0], n_results=len(results), results=results,
                counts={"retrieved": len(results), "truncated": truncated},
            )
        )
        db.commit()


def check(c, pid):
    r = c.get(f"/api/projects/{pid}/known-items")
    assert r.status_code == 200, r.text
    return r.json()


def by_title(data):
    return {i["title"]: i["status"] for i in data["items"]}


LONG = "Remote work and worker productivity evidence"


def test_no_seeds_or_no_searches(world):
    c, pid = world
    data = check(c, pid)
    assert data["seeds"] == 0 and data["flagged"] is False and data["recall_of_seeds"] is None
    seed(c, pid, title=LONG, doi="10.1234/a")
    data = check(c, pid)
    assert by_title(data) == {LONG: "not_searched"} and data["flagged"] is False and data["checked"] == 0


def test_found_by_doi_or_title_and_missing_ones_are_flagged(world):
    c, pid = world
    seed(c, pid, title="Seed by DOI paper", doi="10.1234/Found")
    seed(c, pid, title=LONG, year=2020, authors=["Ada Lovelace"])
    seed(c, pid, title="A paper the search never returned", doi="10.1234/gone")
    run(pid, [entry(1, doi="10.1234/found"), entry(2, title=LONG, year=2020, authors=["Lovelace, Ada"]), entry(3, doi="10.1234/other")])

    data = check(c, pid)

    assert by_title(data) == {"Seed by DOI paper": "found", LONG: "found", "A paper the search never returned": "missing"}
    assert data["flagged"] is True and data["found"] == 2 and data["missing"] == 1 and data["recall_of_seeds"] == 0.667
    assert "1 of 3" in data["flags"][0]


def test_everything_found_is_not_flagged(world):
    c, pid = world
    seed(c, pid, title="Seed by DOI paper", doi="10.1234/a")
    run(pid, [entry(1, doi="10.1234/a")])
    data = check(c, pid)
    assert data["flagged"] is False and data["flags"] == [] and data["recall_of_seeds"] == 1.0


def test_found_by_any_search_and_seed_lists_which(world):
    c, pid = world
    seed(c, pid, title="Seed by DOI paper", doi="10.1234/a")
    run(pid, [entry(1, doi="10.1234/zzz")], database="openalex")
    run(pid, [entry(2, doi="10.1234/a")], database="crossref")
    data = check(c, pid)
    assert data["items"][0]["status"] == "found" and len(data["items"][0]["found_in"]) == 1 and len(data["searches"]) == 2


def test_different_dois_never_match_even_with_the_same_title(world):
    c, pid = world
    seed(c, pid, title=LONG, doi="10.1234/preprint", year=2020)
    run(pid, [entry(1, doi="10.1234/published", title=LONG, year=2020)])
    assert by_title(check(c, pid)) == {LONG: "missing"}


def test_optional_year_and_author_only_count_when_the_seed_gives_them(world):
    c, pid = world
    seed(c, pid, title=LONG)  # title only
    seed(c, pid, title="Another quite long title about teams", year=2019)
    seed(c, pid, title="Third quite long title about firms", year=2018, authors=["Grace Hopper"])
    run(
        pid,
        [
            entry(1, title=LONG, year=2021, authors=["X Y"]),
            entry(2, title="Another quite long title about teams", year=2020),  # wrong year
            entry(3, title="Third quite long title about firms", year=2018, authors=["Alan Turing"]),  # wrong author
        ],
    )
    got = by_title(check(c, pid))
    assert got[LONG] == "found"
    assert got["Another quite long title about teams"] == "missing"
    assert got["Third quite long title about firms"] == "missing"


def test_a_seed_that_cannot_identify_a_paper_is_not_reported_missing(world):
    c, pid = world
    seed(c, pid, title="Short title")
    run(pid, [entry(1, doi="10.1234/a")])
    data = check(c, pid)
    assert by_title(data) == {"Short title": "cannot_check"} and data["flagged"] is False and data["checked"] == 0
    assert any("cannot be checked" in f for f in data["flags"])


def test_a_capped_search_is_named_as_a_possible_reason(world):
    c, pid = world
    seed(c, pid, title=LONG, doi="10.1234/a")
    run(pid, [entry(1, doi="10.1234/b")], truncated=True)
    item = check(c, pid)["items"][0]
    assert item["status"] == "missing" and "cap" in item["note"]


def test_only_the_latest_version_of_a_search_counts(world):
    c, pid = world
    seed(c, pid, title=LONG, doi="10.1234/a")
    sid = uuid.uuid4()
    with SessionLocal() as db:
        for v, results in ((1, [entry(1, doi="10.1234/a")]), (2, [entry(2, doi="10.1234/b")])):
            _clock[0] += timedelta(minutes=1)
            db.add(SearchQuery(project_id=uuid.UUID(pid), search_id=sid, database="openalex", query_string="q", version=v,
                               run_at=_clock[0], results=results, counts={"retrieved": 1, "truncated": False}))
        db.commit()
    assert by_title(check(c, pid)) == {LONG: "missing"}


def test_seed_validation_duplicates_and_removal(world):
    c, pid = world
    sid = seed(c, pid, title=LONG, doi="https://doi.org/10.1234/A")
    assert c.get(f"/api/projects/{pid}/seeds").json()[0]["doi"] == "10.1234/a"
    for bad in ({"title": LONG, "doi": "10.1234/a"}, ):
        assert c.post(f"/api/projects/{pid}/seeds", json=bad).status_code == 409
    for bad in ({"title": "ab"}, {"title": LONG, "doi": "nonsense"}, {"title": LONG, "year": 12}, {"title": LONG, "x": 1}):
        assert c.post(f"/api/projects/{pid}/seeds", json=bad).status_code == 422
    assert c.delete(f"/api/projects/{pid}/seeds/{sid}").status_code == 204
    assert c.delete(f"/api/projects/{pid}/seeds/{sid}").status_code == 404
    actions = [e["action"] for e in c.get(f"/api/projects/{pid}/audit").json()]
    assert "seed.added" in actions and "seed.removed" in actions


def test_another_projects_seeds_and_runs_are_not_used(world):
    c, pid = world
    other = c.post("/api/projects", json={"title": "Other"}).json()["id"]
    seed(c, other, title=LONG, doi="10.1234/a")
    run(pid, [entry(1, doi="10.1234/a")])
    assert check(c, pid)["seeds"] == 0
    assert by_title(check(c, other)) == {LONG: "not_searched"}
