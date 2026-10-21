import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models import ResearchBrief, SearchQuery, Source

THIS_YEAR = datetime.now(timezone.utc).year


@pytest.fixture
def world():
    tag = uuid.uuid4().hex[:8]
    owner = TestClient(app)
    owner.post("/api/auth/development/login", json={"email": f"bias-{tag}@example.com", "display_name": "B"})
    pid = owner.post("/api/projects", json={"title": "Bias"}).json()["id"]
    return {"owner": owner, "pid": uuid.UUID(pid)}


def _search(world, database, *, ran=True):
    with SessionLocal() as db:
        db.add(SearchQuery(
            project_id=world["pid"], database=database, query_string="q",
            run_at=datetime.now(timezone.utc) if ran else None, n_results=5 if ran else None,
        ))
        db.commit()


def _source(world, title, *, year=2015, citations=None, language=None):
    with SessionLocal() as db:
        db.add(Source(project_id=world["pid"], title=title, year=year, citation_count=citations, language=language))
        db.commit()


def _brief(world, min_citations):
    with SessionLocal() as db:
        db.add(ResearchBrief(project_id=world["pid"], topic="t", keyword_groups=[], min_citations=min_citations, updated_by="tester"))
        db.commit()


def warnings(world):
    response = world["owner"].get(f"/api/projects/{world['pid']}/bias-warnings")
    assert response.status_code == 200, response.text
    return {w["code"]: w for w in response.json()["warnings"]}


def test_no_search_run_says_so_instead_of_claiming_coverage(world):
    _search(world, "openalex", ran=False)

    found = warnings(world)

    assert found["database_coverage"]["level"] == "info"
    assert found["database_coverage"]["databases"] == []


def test_a_single_database_is_a_warning_and_two_are_not(world):
    _search(world, "openalex")
    assert warnings(world)["database_coverage"]["level"] == "warning"
    assert warnings(world)["database_coverage"]["databases"] == ["openalex"]

    _search(world, "pubmed")
    assert "database_coverage" not in warnings(world)


def test_a_citation_threshold_reports_what_it_hides_and_how_much_is_recent(world):
    _brief(world, 10)
    _source(world, "old and cited", year=2010, citations=500)
    _source(world, "old and low", year=2010, citations=2)
    _source(world, "new and low", year=THIS_YEAR, citations=0)
    _source(world, "unknown count", year=2014, citations=None)

    warning = warnings(world)["citation_count_bias"]

    assert warning["level"] == "warning"
    assert warning["threshold"] == 10
    assert warning["hidden_below_threshold"] == 2
    assert warning["hidden_recent"] == 1
    assert warning["unknown_count"] == 1


def test_a_source_exactly_recent_years_old_counts_as_recent(world):
    _brief(world, 10)
    _source(world, "exactly two years old", year=THIS_YEAR - 1, citations=0)
    _source(world, "one year older", year=THIS_YEAR - 2, citations=0)

    warning = warnings(world)["citation_count_bias"]

    assert warning["hidden_below_threshold"] == 2
    assert warning["hidden_recent"] == 1


def test_a_source_with_exactly_the_threshold_count_is_not_hidden(world):
    _brief(world, 10)
    _source(world, "exactly at the threshold", year=2010, citations=10)
    _source(world, "just below", year=2010, citations=9)

    warning = warnings(world)["citation_count_bias"]

    assert warning["hidden_below_threshold"] == 1


def test_no_threshold_means_no_citation_warning(world):
    _source(world, "x", citations=1)

    assert "citation_count_bias" not in warnings(world)


def test_language_is_reported_as_not_assessable_because_it_is_not_recorded(world):
    _source(world, "x")

    found = warnings(world)["language_bias"]

    assert found["level"] == "info" and found["assessable"] is False


def test_merged_sources_are_not_counted(world):
    _brief(world, 10)
    with SessionLocal() as db:
        db.add(Source(project_id=world["pid"], title="dup", year=2010, citation_count=1, merged_into=uuid.uuid4()))
        db.commit()

    assert "citation_count_bias" not in warnings(world)


def test_merged_and_screened_out_sources_are_left_out_of_the_language_counts(world):
    _source(world, "english", language="en")
    _source(world, "merged french", language="fr")
    _source(world, "excluded german", language="de")
    with SessionLocal() as db:
        db.get(Source, _id_of(world, "merged french")).merged_into = uuid.uuid4()
        db.commit()
    world["owner"].put(f"/api/projects/{world['pid']}/criteria", json={"framework": "custom", "criteria": [
        {"kind": "include", "text": "Empirical"}, {"kind": "exclude", "text": "Opinion"}]})
    response = world["owner"].post(f"/api/projects/{world['pid']}/screening/decisions",
                                   json={"source_id": str(_id_of(world, "excluded german")), "decision": "exclude", "reason_code": "E1"})
    assert response.status_code == 201, response.text

    found = warnings(world)["language_bias"]

    assert found["by_language"] == {"en": 1}
    assert found["level"] == "warning"


def _id_of(world, title):
    with SessionLocal() as db:
        return db.scalar(select(Source.id).where(Source.project_id == world["pid"], Source.title == title))
