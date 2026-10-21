import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.connectors import crossref, openalex
from app.connectors.base import language_code
from app.database import SessionLocal
from app.main import app
from app.models import Source


def test_language_code_keeps_only_a_two_letter_code():
    assert language_code("EN") == "en"
    assert language_code(" de ") == "de"
    for bad in (None, "", "eng", "e1", 3, "english"):
        assert language_code(bad) is None


def test_openalex_and_crossref_records_carry_the_language_they_report():
    work = {"id": "https://openalex.org/W1", "display_name": "T", "language": "es"}
    assert openalex.to_record(work).language == "es"
    assert openalex.to_record({"id": "https://openalex.org/W2", "display_name": "T"}).language is None
    item = {"DOI": "10.1000/x", "title": ["T"], "language": "FR"}
    assert crossref.to_record(item).language == "fr"


@pytest.fixture
def world():
    tag = uuid.uuid4().hex[:8]
    owner = TestClient(app)
    owner.post("/api/auth/development/login", json={"email": f"lang-{tag}@example.com", "display_name": "L"})
    pid = owner.post("/api/projects", json={"title": "Lang"}).json()["id"]
    return {"owner": owner, "pid": uuid.UUID(pid)}


def _source(world, title, language):
    with SessionLocal() as db:
        db.add(Source(project_id=world["pid"], title=title, language=language))
        db.commit()


def _language_warning(world):
    body = world["owner"].get(f"/api/projects/{world['pid']}/bias-warnings").json()
    return {w["code"]: w for w in body["warnings"]}["language_bias"]


def test_with_no_known_language_the_check_stays_not_assessable(world):
    _source(world, "a", None)

    warning = _language_warning(world)

    assert warning["assessable"] is False


def test_only_one_known_language_is_a_warning_with_counts(world):
    _source(world, "a", "en")
    _source(world, "b", "en")
    _source(world, "c", None)

    warning = _language_warning(world)

    assert warning["assessable"] is True and warning["level"] == "warning"
    assert warning["by_language"] == {"en": 2} and warning["unknown_language"] == 1


def test_several_languages_are_reported_without_a_warning(world):
    _source(world, "a", "en")
    _source(world, "b", "de")

    warning = _language_warning(world)

    assert warning["level"] == "info" and warning["by_language"] == {"de": 1, "en": 1}


def test_the_source_api_shows_the_language(world):
    _source(world, "a", "pt")

    listed = world["owner"].get(f"/api/projects/{world['pid']}/sources").json()

    assert [s["language"] for s in listed] == ["pt"]
