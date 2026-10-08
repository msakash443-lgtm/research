"""Research runs use schema-validated output with confidence and abstention (plan M0.8.6)."""
import json

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from tests.llm_replies import abstention, chat_reply, structured


def _setup():
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": "structured@example.com", "display_name": "S"})
    pid = client.post("/api/projects", json={"title": "Structured"}).json()["id"]
    sid = client.post(f"/api/projects/{pid}/sources", json={"title": "Official release", "evidence_excerpt": "Output rose 4%.", "locator": "p1"}).json()["id"]
    client.post(f"/api/projects/{pid}/sources/{sid}/verify")
    return client, pid


def _run(client, pid):
    response = client.post(f"/api/projects/{pid}/research-runs", json={"question": "Did output change at all?"})
    assert response.status_code == 202, response.text
    return client.get(f"/api/projects/{pid}/research-runs").json()[0]


def _content(payload):
    return {"choices": [{"message": {"content": payload if isinstance(payload, str) else json.dumps(payload)}}]}


def test_an_answer_is_stored_with_its_confidence_and_the_v4_prompt(fake_llm):
    fake_llm.handler = lambda r: chat_reply("Output rose [S1].", 0.65)
    run = _run(*_setup())
    assert run["status"] == "completed" and run["answer"] == "Output rose [S1]."
    assert run["confidence"] == 0.65 and run["insufficient_evidence"] is False
    assert run["prompt_version"] == "evidence_synthesis@4"


def test_an_abstention_completes_without_putting_its_reason_in_the_answer(fake_llm):
    fake_llm.handler = lambda r: _content(abstention("No source reports wages."))
    run = _run(*_setup())
    assert run["status"] == "completed" and run["answer"] is None
    assert run["insufficient_evidence"] is True and run["insufficient_reason"] == "No source reports wages."


def test_a_reply_that_is_not_the_agreed_shape_fails_the_run_loudly_after_retries(fake_llm):
    fake_llm.handler = lambda r: _content("Just some prose [S1].")
    run = _run(*_setup())
    assert run["status"] == "failed" and run["answer"] is None and run["error_message"]
    assert len(fake_llm.requests) == get_settings().llm_schema_max_attempts


def test_an_answer_that_breaks_the_abstention_rules_is_rejected_not_repaired(fake_llm):
    bad = structured("An answer", 0.9)
    bad["insufficient_evidence"] = {"insufficient": True, "reason": "but I answered anyway"}
    fake_llm.handler = lambda r: _content(bad)
    run = _run(*_setup())
    assert run["status"] == "failed" and run["answer"] is None


def test_citation_rules_still_apply_to_the_structured_answer(fake_llm):
    fake_llm.handler = lambda r: chat_reply("Output rose [S9].")
    run = _run(*_setup())
    assert run["status"] == "failed" and run["answer"] is None and "[S9]" in run["error_message"]
