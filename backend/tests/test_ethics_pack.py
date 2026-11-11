import uuid

from fastapi.testclient import TestClient

from app.ethics_pack import build_ethics_pack
from app.main import app


def test_the_pack_has_the_four_documents_in_a_fixed_order():
    docs = build_ethics_pack("Trust in teams")
    assert [d.key for d in docs] == ["information_sheet", "consent_form", "data_management_plan", "irb_skeleton"]


def test_the_study_title_and_research_question_are_filled_into_every_document():
    docs = build_ethics_pack("Trust in teams", "Effect of trust on sharing", "Does trust predict sharing?")
    assert all("Trust in teams" in d.body for d in docs if d.key != "data_management_plan")
    assert "Does trust predict sharing?" in next(d for d in docs if d.key == "irb_skeleton").body


def test_facts_the_app_does_not_know_are_left_as_fields_not_invented():
    docs = build_ethics_pack("Trust in teams")
    body = "\n".join(d.body for d in docs)
    assert "[ethics committee contact: to complete]" in body
    assert "[retention period and deletion date: to complete]" in body
    assert "[research question: to complete]" in body


def test_the_dmp_does_not_state_as_fact_that_only_the_local_model_sees_data():
    dmp = next(d for d in build_ethics_pack("Trust in teams") if d.key == "data_management_plan").body
    assert "processed only by the local model endpoint" not in dmp
    assert "[third-party processing: confirm the local model endpoint is the only processor, or none: to complete]" in dmp


def test_blank_title_and_question_are_marked_for_completion_not_left_empty():
    docs = build_ethics_pack("   ", None, "")
    irb = next(d for d in docs if d.key == "irb_skeleton").body
    assert "[study title: to complete]" in irb
    assert "[research question: to complete]" in irb


def test_the_route_returns_the_pack_and_states_that_nothing_is_approved():
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": f"ethics-{uuid.uuid4().hex}@example.com", "display_name": "E"})
    pid = client.post("/api/projects", json={"title": "Ethics route"}).json()["id"]
    response = client.get(f"/api/projects/{pid}/ethics-pack")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["gate"] == "G7"
    assert "nothing here approves it" in payload["note"]
    assert [d["key"] for d in payload["documents"]][0] == "information_sheet"
    assert "Ethics route" in payload["documents"][0]["body"]
