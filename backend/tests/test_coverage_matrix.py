import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Extraction, ScreeningDecision, Source

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


@pytest.fixture
def world():
    tag = uuid.uuid4().hex[:8]
    owner = TestClient(app)
    owner.post("/api/auth/development/login", json={"email": f"cov-{tag}@example.com", "display_name": "C"})
    pid = owner.post("/api/projects", json={"title": "Coverage"}).json()["id"]
    return {"owner": owner, "pid": pid}


def paper(world, title, design=None, population=None, country=None, *, verified=False, at=0, source_id=None):
    """A source (unless given) with one extraction stored directly; returns the source id."""
    if source_id is None:
        source_id = world["owner"].post(f"/api/projects/{world['pid']}/sources", json={"title": title}).json()["id"]
    fields = {"method": {"design": design, "analysis": None}, "sample": {"n": None, "population": population, "country": country}}
    with SessionLocal() as db:
        when = T0 + timedelta(hours=at)
        db.add(Extraction(
            project_id=uuid.UUID(world["pid"]), source_id=uuid.UUID(source_id), schema_version="default@1", fields_json=fields,
            evidence_spans={}, extracted_by="human", extractor="tester", verified_by_human=verified,
            verified_by="tester" if verified else None, verified_at=when if verified else None, created_at=when, updated_at=when,
        ))
        db.commit()
    return source_id


def matrix(world, **params):
    response = world["owner"].get(f"/api/projects/{world['pid']}/coverage-matrix", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def cell(data, row, col, index=0):
    found = [c for c in data["matrices"][index]["cells"] if (c["row"], c["col"]) == (row, col)]
    return found[0] if found else None


def test_counts_papers_per_cell_and_lists_the_empty_ones(world):
    paper(world, "A", "Survey", "Nurses", "UK")
    paper(world, "B", "Survey", "Nurses", "India")
    paper(world, "C", "Interviews", "Teachers", "UK")
    data = matrix(world)
    assert (data["rows"], data["cols"], data["layer"]) == ("method", "population", None)
    assert data["row_values"] == ["Interviews", "Survey"] and data["col_values"] == ["Nurses", "Teachers"]
    survey_nurses = cell(data, "Survey", "Nurses")
    assert survey_nurses["count"] == 2 and sorted(p["title"] for p in survey_nurses["papers"]) == ["A", "B"]
    empty = {(e["row"], e["col"]) for e in data["matrices"][0]["empty_cells"]}
    assert empty == {("Interviews", "Nurses"), ("Survey", "Teachers")}
    assert "not evidence that no research exists" in data["note"]
    assert data["matrices"][0]["empty_cells"][0]["cell_id"].startswith("method=")


def test_spellings_are_grouped_and_shown_with_the_most_common_one(world):
    paper(world, "A", "Survey", "Nurses")
    paper(world, "B", "survey", "nurses")
    paper(world, "C", "  Survey ", "Nurses")
    data = matrix(world)
    assert data["row_values"] == ["Survey"] and data["col_values"] == ["Nurses"]
    assert cell(data, "Survey", "Nurses")["count"] == 3


def test_missing_values_are_counted_as_not_reported_and_never_placed(world):
    paper(world, "A", "Survey", "Nurses")
    paper(world, "B", None, "Nurses")
    paper(world, "C", "Survey", "   ")
    with SessionLocal() as db:  # a non-text value is not a value
        sid = world["owner"].post(f"/api/projects/{world['pid']}/sources", json={"title": "D"}).json()["id"]
        db.add(Extraction(project_id=uuid.UUID(world["pid"]), source_id=uuid.UUID(sid), schema_version="default@1",
                          fields_json={"method": {"design": 42}, "sample": "Nurses"}, evidence_spans={}, extracted_by="human", extractor="t"))
        db.commit()
    data = matrix(world)
    assert (data["n_papers"], data["n_placed"]) == (4, 1)
    assert data["not_reported"] == {"method": 2, "population": 2}


def test_a_verified_extraction_wins_over_a_newer_unverified_one(world):
    sid = paper(world, "A", "Survey", "Nurses", verified=True, at=1)
    paper(world, "A", "Interviews", "Nurses", at=5, source_id=sid)
    other = paper(world, "B", "Survey", "Teachers", at=1)
    paper(world, "B", "Experiment", "Teachers", at=9, source_id=other)  # newest wins when none is verified
    data = matrix(world)
    assert cell(data, "Survey", "Nurses")["count"] == 1 and cell(data, "Survey", "Nurses")["verified"] == 1
    assert cell(data, "Interviews", "Nurses") is None
    assert cell(data, "Experiment", "Teachers")["count"] == 1 and cell(data, "Survey", "Teachers") is None


def test_verified_only_leaves_out_unverified_papers(world):
    paper(world, "A", "Survey", "Nurses", verified=True)
    paper(world, "B", "Interviews", "Teachers")
    data = matrix(world, verified_only="true")
    assert data["verified_only"] is True and data["n_papers"] == 1 and data["row_values"] == ["Survey"]


def test_a_layer_gives_one_matrix_per_country_with_shared_axes(world):
    paper(world, "A", "Survey", "Nurses", "UK")
    paper(world, "B", "Interviews", "Teachers", "India")
    data = matrix(world, rows="method", cols="population", layer="country")
    assert [m["layer"] for m in data["matrices"]] == ["India", "UK"]
    india = data["matrices"][0]
    assert india["n_papers"] == 1 and len(india["empty_cells"]) == 3  # 2×2 shared axes, one filled
    assert all(e["cell_id"].endswith("|country=india") for e in india["empty_cells"])


def test_merged_and_person_excluded_papers_are_left_out(world):
    kept = paper(world, "Kept", "Survey", "Nurses")
    excluded = paper(world, "Excluded", "Interviews", "Teachers")
    merged = paper(world, "Merged", "Experiment", "Students")
    with SessionLocal() as db:
        db.add(ScreeningDecision(project_id=uuid.UUID(world["pid"]), source_id=uuid.UUID(excluded), stage="full_text", seq=1,
                                 decision="exclude", reason_code="E1", decided_by="human", decider="tester"))
        db.get(Source, uuid.UUID(merged)).merged_into = uuid.UUID(kept)
        db.commit()
    data = matrix(world)
    assert data["n_papers"] == 1 and data["row_values"] == ["Survey"]


@pytest.mark.parametrize("params", [{"rows": "method", "cols": "method"}, {"rows": "design"}, {"layer": "population"}])
def test_bad_dimensions_are_refused(world, params):
    response = world["owner"].get(f"/api/projects/{world['pid']}/coverage-matrix", params=params)
    assert response.status_code == 422


def test_an_empty_project_gives_an_empty_matrix(world):
    data = matrix(world, layer="country")
    assert (data["n_papers"], data["matrices"], data["row_values"]) == (0, [], [])
