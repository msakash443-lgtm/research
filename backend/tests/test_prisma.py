import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import SearchQuery


def _login(email):
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": email, "display_name": "P"})
    return client


@pytest.fixture
def project():
    tag = uuid.uuid4().hex[:8]
    owner = _login(f"prisma-{tag}@example.com")
    pid = owner.post("/api/projects", json={"title": "Prisma"}).json()["id"]
    return owner, uuid.UUID(pid), _login(f"prisma-out-{tag}@example.com")


def entry(i, doi=None, key=None):
    return {"id": f"x:{i}", "doi": doi, "work_key": key}


_clock = [datetime(2026, 1, 1, tzinfo=timezone.utc)]


def add_run(pid, database, results, retrieved=None, search_id=None, version=1, truncated=False):
    _clock[0] += timedelta(minutes=1)
    with SessionLocal() as db:
        row = SearchQuery(
            project_id=pid, search_id=search_id or uuid.uuid4(), database=database, query_string="q", version=version,
            run_at=_clock[0], n_results=len(results), exact=True, caveats=[], results=results,
            counts={"retrieved": retrieved if retrieved is not None else len(results), "truncated": truncated},
        )
        db.add(row)
        db.commit()
        return row.search_id


def flow(client, pid):
    r = client.get(f"/api/projects/{pid}/prisma")
    assert r.status_code == 200, r.text
    return r.json()


def test_empty_project_reports_zeros_and_unavailable_screening(project):
    owner, pid, _ = project
    data = flow(owner, pid)
    assert data["identification"]["identified"] == 0 and data["identification"]["duplicates_removed"] == 0
    assert data["identification"]["searches"] == []
    assert data["screening"] == {"available": False, "screened": None, "excluded_by_reason": None, "included": None}


def test_counts_sum_databases_and_remove_cross_database_duplicates(project):
    owner, pid, _ = project
    add_run(pid, "openalex", [entry(1, "10.1/a"), entry(2, None, "k2"), entry(3, "10.1/c")], retrieved=4)  # 1 repeat within run
    add_run(pid, "crossref", [entry(4, "10.1/a"), entry(5, None, "k2"), entry(6, None, "k6")])
    i = flow(owner, pid)["identification"]
    assert i["identified"] == 7
    assert i["after_duplicates_removed"] == 4  # a, k2, c, k6
    assert i["duplicates_removed"] == 3
    assert i["identified"] - i["duplicates_removed"] == i["after_duplicates_removed"]
    assert [s["database"] for s in i["searches"]] == ["openalex", "crossref"]


def test_different_dois_are_never_merged_even_with_same_title_key(project):
    owner, pid, _ = project
    add_run(pid, "openalex", [entry(1, "10.1/pre", "same")])
    add_run(pid, "crossref", [entry(2, "10.1/pub", "same")])
    assert flow(owner, pid)["identification"]["after_duplicates_removed"] == 2


def test_only_the_latest_version_of_a_search_counts(project):
    owner, pid, _ = project
    sid = add_run(pid, "openalex", [entry(1, "10.1/a"), entry(2, "10.1/b")])
    add_run(pid, "openalex", [entry(3, "10.1/c")], search_id=sid, version=2)
    i = flow(owner, pid)["identification"]
    assert i["identified"] == 1 and len(i["searches"]) == 1 and i["searches"][0]["version"] == 2


def test_truncated_run_is_flagged_as_lower_bound(project):
    owner, pid, _ = project
    add_run(pid, "openalex", [entry(1, "10.1/a")], truncated=True)
    data = flow(owner, pid)
    assert data["identification"]["searches"][0]["truncated"] is True
    assert len(data["warnings"]) == 1 and "lower bound" in data["warnings"][0]


def test_other_projects_runs_are_not_counted_and_outsiders_are_refused(project):
    owner, pid, outsider = project
    other = owner.post("/api/projects", json={"title": "Other"}).json()["id"]
    add_run(uuid.UUID(other), "openalex", [entry(1, "10.1/a")])
    assert flow(owner, pid)["identification"]["identified"] == 0
    assert outsider.get(f"/api/projects/{pid}/prisma").status_code == 404
    assert TestClient(app).get(f"/api/projects/{pid}/prisma").status_code == 401
