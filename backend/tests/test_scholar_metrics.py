import uuid

from fastapi.testclient import TestClient

from app import suggestion_store
from app.database import SessionLocal
from app.main import app
from app.models import Document
from app.scholar_metrics import corrections_per_100, readiness_summary, scholar_metrics, suggestion_review_counts
from app.suggestions import ACCEPTED, DISCARDED, EDITED_AND_ACCEPTED


def test_corrections_per_100_is_none_until_something_is_decided():
    assert corrections_per_100(0, 0) is None


def test_corrections_per_100_rounds_to_one_decimal():
    assert corrections_per_100(4, 1) == 25.0
    assert corrections_per_100(3, 1) == 33.3


def test_a_suggestion_with_no_decision_is_not_counted():
    assert suggestion_review_counts([[], ["created"]]) == {"reviewed": 0, "corrected": 0}


def test_accepted_as_is_is_reviewed_but_not_corrected():
    assert suggestion_review_counts([[ACCEPTED]]) == {"reviewed": 1, "corrected": 0}


def test_edited_then_accepted_and_discarded_are_both_corrections():
    assert suggestion_review_counts([[EDITED_AND_ACCEPTED], [DISCARDED]]) == {"reviewed": 2, "corrected": 2}


def test_a_suggestion_with_several_events_counts_once():
    assert suggestion_review_counts([[ACCEPTED, EDITED_AND_ACCEPTED]]) == {"reviewed": 1, "corrected": 1}


def test_readiness_counts_drafted_sections_and_marks_approval_not_recorded():
    result = readiness_summary([("Intro", True), ("Methods", False)])

    assert result["sections_total"] == 2
    assert result["sections_drafted"] == 1
    assert result["sections_approved"] is None
    assert result["status"] == "not_recorded"


def test_time_saved_is_reported_as_not_recorded_never_as_zero():
    result = scholar_metrics([], [])

    assert result["time_saved"]["status"] == "not_recorded"
    assert "time_saved" in result and "minutes" not in result["time_saved"]


def test_scholar_metrics_api_on_an_empty_project():
    tag = uuid.uuid4().hex[:8]
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": f"sm-{tag}@example.com", "display_name": "S"})
    project_id = client.post("/api/projects", json={"title": "Scholar metrics"}).json()["id"]

    response = client.get(f"/api/projects/{project_id}/scholar-metrics")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["human_corrections"]["reviewed_ai_outputs"] == 0
    assert body["human_corrections"]["per_100_ai_outputs"] is None
    assert body["manuscript_readiness"]["sections_total"] == 0
    assert body["time_saved"]["status"] == "not_recorded"


def test_scholar_metrics_api_counts_real_decisions_and_drafted_sections():
    tag = uuid.uuid4().hex[:8]
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": f"sm2-{tag}@example.com", "display_name": "S"})
    project_id = uuid.UUID(client.post("/api/projects", json={"title": "Scholar metrics 2"}).json()["id"])

    # Four decided AI outputs (two accepted as is, one edited, one discarded) and one still pending.
    decisions = [("accepted", None), ("accepted", None), (EDITED_AND_ACCEPTED, "a better sentence"), ("discarded", None)]
    with SessionLocal() as db:
        for kind, text in decisions:
            suggestion = suggestion_store.create_suggestion(
                db, project_id=project_id, target_kind="note", target_id=str(uuid.uuid4()), content="AI text",
                model_id="test-model", prompt_version="v1", producing_agent="tester",
            )
            suggestion_store.act(db, suggestion, kind, "tester", text=text)
        suggestion_store.create_suggestion(
            db, project_id=project_id, target_kind="note", target_id=str(uuid.uuid4()), content="still pending",
            model_id="test-model", prompt_version="v1", producing_agent="tester",
        )
        db.add_all([
            Document(project_id=project_id, title="Intro", position=0, created_by="tester", current_version_id=uuid.uuid4()),
            Document(project_id=project_id, title="Methods", position=1, created_by="tester"),
        ])
        db.commit()

    response = client.get(f"/api/projects/{project_id}/scholar-metrics")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["human_corrections"]["reviewed_ai_outputs"] == 4
    assert body["human_corrections"]["corrected_ai_outputs"] == 2
    assert body["human_corrections"]["per_100_ai_outputs"] == 50.0
    assert body["manuscript_readiness"]["sections_total"] == 2
    assert body["manuscript_readiness"]["sections_drafted"] == 1
