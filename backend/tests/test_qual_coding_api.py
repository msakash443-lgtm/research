import json
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent import llm as llm_module
from app.config import Settings
from app.database import SessionLocal
from app.main import app
from app.models import AuditEvent, QualExcerpt, QualSuggestion
from app.routers import qual_coding as router_module

EXCERPT = "My manager never explains decisions, so I stopped asking."
LOCAL = "http://localhost:11434/v1"
THIRD_PARTY = "https://third-party.example/v1"


def _reply(*codes: str) -> str:
    return json.dumps([{"code": c, "rationale": "from the excerpt"} for c in codes])


class FakeHttp:
    """Stands in for the model's network calls. It records every URL the LLM adapter posts to and answers with `reply`.
    Only the LLM module's `httpx` is replaced, so the test client keeps working."""

    def __init__(self, reply: str):
        self.reply = reply
        self.urls: list[str] = []

    def namespace(self):
        http = self

        class _Client:
            def __init__(self, **_kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            def post(self, url, **_kwargs):
                http.urls.append(url)
                return _Response(http.reply)

        return type("httpx_stub", (), {"Client": _Client, "HTTPError": httpx.HTTPError})


class _Response:
    def __init__(self, content: str):
        self._content = content
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": self._content}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


def _settings(**overrides) -> Settings:
    base = {"local_llm_api_base_url": LOCAL, "local_llm_model": "qwen2.5:7b-instruct",
            "llm_api_base_url": THIRD_PARTY, "llm_model": "third-party-model", "llm_api_key": "x"}
    return Settings(_env_file=None, **{**base, **overrides})


@pytest.fixture()
def project(monkeypatch):
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": f"qc-{uuid.uuid4().hex}@example.com", "display_name": "Q"})
    pid = client.post("/api/projects", json={"title": "Coding"}).json()["id"]
    return client, pid


def _patch(monkeypatch, http: FakeHttp, settings: Settings) -> None:
    monkeypatch.setattr(router_module, "get_settings", lambda: settings)
    monkeypatch.setattr(llm_module, "httpx", http.namespace())


def test_an_excerpt_is_stored_with_pending_suggestions(project, monkeypatch):
    client, pid = project
    http = FakeHttp(_reply("trust", "silence"))
    _patch(monkeypatch, http, _settings())
    r = client.post(f"/api/projects/{pid}/qual/suggest", json={"excerpt": EXCERPT})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["text"] == EXCERPT
    assert [s["status"] for s in body["suggestions"]] == ["pending", "pending"]
    listed = client.get(f"/api/projects/{pid}/qual/excerpts").json()
    assert [e["id"] for e in listed] == [body["id"]]


def test_the_audit_log_holds_ids_and_counts_never_the_excerpt(project, monkeypatch):
    client, pid = project
    _patch(monkeypatch, FakeHttp(_reply("trust")), _settings())
    client.post(f"/api/projects/{pid}/qual/suggest", json={"excerpt": EXCERPT})
    with SessionLocal() as db:
        events = db.scalars(select(AuditEvent).where(AuditEvent.action == "qual.suggested")).all()
        assert events and all(EXCERPT not in str(e.payload_json) for e in events)


def test_participant_text_goes_only_to_the_local_endpoint_even_when_the_project_is_routed_to_the_third_party(project, monkeypatch):
    # M5.6.1 gap: an end-to-end check that participant text cannot reach the third-party route.
    client, pid = project
    http = FakeHttp(_reply("trust"))
    _patch(monkeypatch, http, _settings())
    assert client.post(f"/api/projects/{pid}/qual/suggest", json={"excerpt": EXCERPT}).status_code == 201
    assert http.urls and all(url.startswith(LOCAL) for url in http.urls)
    assert not any("third-party" in url for url in http.urls)


def test_without_a_local_model_nothing_is_sent_anywhere(project, monkeypatch):
    client, pid = project
    http = FakeHttp(_reply("trust"))
    _patch(monkeypatch, http, _settings(local_llm_api_base_url=None, local_llm_model=None))
    r = client.post(f"/api/projects/{pid}/qual/suggest", json={"excerpt": EXCERPT})
    assert r.status_code == 503 and "locally configured model" in r.json()["detail"]
    assert http.urls == []


def test_a_model_reply_that_is_not_json_is_502_and_stores_nothing(project, monkeypatch):
    client, pid = project
    _patch(monkeypatch, FakeHttp("Here are some codes: trust"), _settings())
    r = client.post(f"/api/projects/{pid}/qual/suggest", json={"excerpt": EXCERPT})
    assert r.status_code == 502
    assert client.get(f"/api/projects/{pid}/qual/excerpts").json() == []


def test_a_suggestion_can_be_accepted_once(project, monkeypatch):
    client, pid = project
    _patch(monkeypatch, FakeHttp(_reply("trust")), _settings())
    excerpt = client.post(f"/api/projects/{pid}/qual/suggest", json={"excerpt": EXCERPT}).json()
    sid = excerpt["suggestions"][0]["id"]
    r = client.post(f"/api/projects/{pid}/qual/decisions", json={"suggestion_id": sid, "decision": "accept"})
    assert r.status_code == 200 and r.json()["status"] == "accepted" and r.json()["decided_by"]
    again = client.post(f"/api/projects/{pid}/qual/decisions", json={"suggestion_id": sid, "decision": "reject"})
    assert again.status_code == 409


def test_a_rejected_suggestion_is_not_offered_as_an_existing_code_later(project, monkeypatch):
    client, pid = project
    http = FakeHttp(_reply("trust"))
    _patch(monkeypatch, http, _settings())
    first = client.post(f"/api/projects/{pid}/qual/suggest", json={"excerpt": EXCERPT}).json()
    sid = first["suggestions"][0]["id"]
    client.post(f"/api/projects/{pid}/qual/decisions", json={"suggestion_id": sid, "decision": "reject"})
    client.post(f"/api/projects/{pid}/qual/suggest", json={"excerpt": EXCERPT})
    # only accepted codes are offered as the existing codebook
    assert http.urls and len(http.urls) == 2


def test_a_suggestion_of_another_project_cannot_be_decided(project, monkeypatch):
    client, pid = project
    _patch(monkeypatch, FakeHttp(_reply("trust")), _settings())
    sid = client.post(f"/api/projects/{pid}/qual/suggest", json={"excerpt": EXCERPT}).json()["suggestions"][0]["id"]
    other = client.post("/api/projects", json={"title": "Other"}).json()["id"]
    r = client.post(f"/api/projects/{other}/qual/decisions", json={"suggestion_id": sid, "decision": "accept"})
    assert r.status_code == 404


def test_a_blank_or_oversized_excerpt_is_refused_before_any_call(project, monkeypatch):
    client, pid = project
    http = FakeHttp(_reply("trust"))
    _patch(monkeypatch, http, _settings())
    assert client.post(f"/api/projects/{pid}/qual/suggest", json={"excerpt": "   "}).status_code == 422
    assert client.post(f"/api/projects/{pid}/qual/suggest", json={"excerpt": "x" * 5001}).status_code == 422
    assert http.urls == []


def test_accepting_a_suggestion_adds_it_to_the_codebook_with_the_rationale_as_its_definition(project, monkeypatch):
    client, pid = project
    _patch(monkeypatch, FakeHttp(_reply("trust")), _settings())
    excerpt = client.post(f"/api/projects/{pid}/qual/suggest", json={"excerpt": EXCERPT}).json()
    sid = excerpt["suggestions"][0]["id"]
    assert client.get(f"/api/projects/{pid}/qual/codebook").json() == {"codes": {}}
    client.post(f"/api/projects/{pid}/qual/decisions", json={"suggestion_id": sid, "decision": "accept"})
    book = client.get(f"/api/projects/{pid}/qual/codebook").json()
    assert book == {"codes": {"trust": "from the excerpt"}}


def test_accepting_a_suggestion_for_a_code_already_in_the_codebook_does_not_duplicate_it(project, monkeypatch):
    client, pid = project
    _patch(monkeypatch, FakeHttp(_reply("trust")), _settings())
    first = client.post(f"/api/projects/{pid}/qual/suggest", json={"excerpt": EXCERPT}).json()
    client.post(f"/api/projects/{pid}/qual/decisions",
                json={"suggestion_id": first["suggestions"][0]["id"], "decision": "accept"})
    second = client.post(f"/api/projects/{pid}/qual/suggest", json={"excerpt": EXCERPT}).json()
    r = client.post(f"/api/projects/{pid}/qual/decisions",
                     json={"suggestion_id": second["suggestions"][0]["id"], "decision": "accept"})
    assert r.status_code == 200
    assert client.get(f"/api/projects/{pid}/qual/codebook").json() == {"codes": {"trust": "from the excerpt"}}


def test_a_rejected_suggestion_never_reaches_the_codebook(project, monkeypatch):
    client, pid = project
    _patch(monkeypatch, FakeHttp(_reply("trust")), _settings())
    excerpt = client.post(f"/api/projects/{pid}/qual/suggest", json={"excerpt": EXCERPT}).json()
    sid = excerpt["suggestions"][0]["id"]
    client.post(f"/api/projects/{pid}/qual/decisions", json={"suggestion_id": sid, "decision": "reject"})
    assert client.get(f"/api/projects/{pid}/qual/codebook").json() == {"codes": {}}


def test_rename_moves_the_definition_and_the_old_name_is_no_longer_offered(project, monkeypatch):
    client, pid = project
    _patch(monkeypatch, FakeHttp(_reply("trust")), _settings())
    excerpt = client.post(f"/api/projects/{pid}/qual/suggest", json={"excerpt": EXCERPT}).json()
    client.post(f"/api/projects/{pid}/qual/decisions",
                json={"suggestion_id": excerpt["suggestions"][0]["id"], "decision": "accept"})
    r = client.post(f"/api/projects/{pid}/qual/codebook/rename", json={"old": "trust", "new": "distrust-cues"})
    assert r.status_code == 200
    assert r.json() == {"codes": {"distrust-cues": "from the excerpt"}}
    assert client.get(f"/api/projects/{pid}/qual/codebook").json() == {"codes": {"distrust-cues": "from the excerpt"}}


def test_merge_drops_the_source_from_the_live_codebook_but_keeps_its_row(project, monkeypatch):
    client, pid = project
    http = FakeHttp(_reply("trust"))
    _patch(monkeypatch, http, _settings())
    first = client.post(f"/api/projects/{pid}/qual/suggest", json={"excerpt": EXCERPT}).json()
    client.post(f"/api/projects/{pid}/qual/decisions",
                json={"suggestion_id": first["suggestions"][0]["id"], "decision": "accept"})
    http.reply = _reply("silence")
    second = client.post(f"/api/projects/{pid}/qual/suggest", json={"excerpt": "A different excerpt."}).json()
    client.post(f"/api/projects/{pid}/qual/decisions",
                json={"suggestion_id": second["suggestions"][0]["id"], "decision": "accept"})
    r = client.post(f"/api/projects/{pid}/qual/codebook/merge", json={"source": "trust", "target": "silence"})
    assert r.status_code == 200
    assert r.json() == {"codes": {"silence": "from the excerpt"}}
    with SessionLocal() as db:
        from app.models import QualCode
        row = db.scalars(select(QualCode).where(QualCode.project_id == uuid.UUID(pid), QualCode.name == "trust")).one()
        assert row.definition == "from the excerpt" and row.redirect_to == "silence"


def test_renaming_or_merging_an_unknown_code_is_refused(project, monkeypatch):
    client, pid = project
    assert client.post(f"/api/projects/{pid}/qual/codebook/rename",
                        json={"old": "nope", "new": "also-nope"}).status_code == 409
    assert client.post(f"/api/projects/{pid}/qual/codebook/merge",
                        json={"source": "nope", "target": "also-nope"}).status_code == 409


def test_renaming_onto_a_name_in_use_or_merging_into_an_unknown_target_is_refused(project, monkeypatch):
    client, pid = project
    http = FakeHttp(_reply("trust"))
    _patch(monkeypatch, http, _settings())
    first = client.post(f"/api/projects/{pid}/qual/suggest", json={"excerpt": EXCERPT}).json()
    client.post(f"/api/projects/{pid}/qual/decisions",
                json={"suggestion_id": first["suggestions"][0]["id"], "decision": "accept"})
    http.reply = _reply("silence")
    second = client.post(f"/api/projects/{pid}/qual/suggest", json={"excerpt": "A different excerpt."}).json()
    client.post(f"/api/projects/{pid}/qual/decisions",
                json={"suggestion_id": second["suggestions"][0]["id"], "decision": "accept"})
    assert client.post(f"/api/projects/{pid}/qual/codebook/rename",
                        json={"old": "trust", "new": "silence"}).status_code == 409
    assert client.post(f"/api/projects/{pid}/qual/codebook/merge",
                        json={"source": "trust", "target": "ghost"}).status_code == 409
    assert client.get(f"/api/projects/{pid}/qual/codebook").json() == {
        "codes": {"trust": "from the excerpt", "silence": "from the excerpt"}}


def test_with_ai_off_a_suggest_request_is_refused_before_any_model_call(project, monkeypatch):
    client, pid = project
    http = FakeHttp(_reply("trust"))
    _patch(monkeypatch, http, _settings())
    assert client.put(f"/api/projects/{pid}/ai-settings", json={"ai_involvement": "off"}).status_code == 200
    r = client.post(f"/api/projects/{pid}/qual/suggest", json={"excerpt": EXCERPT})
    assert r.status_code == 409
    assert http.urls == []
    assert client.get(f"/api/projects/{pid}/qual/excerpts").json() == []


def test_a_spent_token_budget_refuses_the_next_suggest_with_409_and_stores_nothing(project, monkeypatch):
    client, pid = project
    http = FakeHttp(_reply("trust"))
    _patch(monkeypatch, http, _settings())
    assert client.put(f"/api/projects/{pid}/usage/budget", json={"override": 2}).status_code == 200
    assert client.post(f"/api/projects/{pid}/qual/suggest", json={"excerpt": EXCERPT}).status_code == 201
    calls_before = len(http.urls)
    r = client.post(f"/api/projects/{pid}/qual/suggest", json={"excerpt": "A second excerpt."})
    assert r.status_code == 409
    assert len(http.urls) == calls_before
    assert len(client.get(f"/api/projects/{pid}/qual/excerpts").json()) == 1


def test_a_suggestion_with_no_rationale_is_accepted_when_the_person_writes_the_definition(project, monkeypatch):
    client, pid = project
    _patch(monkeypatch, FakeHttp(json.dumps([{"code": "trust", "rationale": ""}])), _settings())
    excerpt = client.post(f"/api/projects/{pid}/qual/suggest", json={"excerpt": EXCERPT}).json()
    sid = excerpt["suggestions"][0]["id"]
    r = client.post(f"/api/projects/{pid}/qual/decisions",
                    json={"suggestion_id": sid, "decision": "accept", "definition": "Belief that others act in good faith."})
    assert r.status_code == 200, r.text
    assert client.get(f"/api/projects/{pid}/qual/codebook").json() == {
        "codes": {"trust": "Belief that others act in good faith."}}


def test_a_suggestion_with_no_rationale_and_no_definition_is_refused_and_stays_pending(project, monkeypatch):
    client, pid = project
    _patch(monkeypatch, FakeHttp(json.dumps([{"code": "trust", "rationale": "   "}])), _settings())
    excerpt = client.post(f"/api/projects/{pid}/qual/suggest", json={"excerpt": EXCERPT}).json()
    sid = excerpt["suggestions"][0]["id"]
    r = client.post(f"/api/projects/{pid}/qual/decisions", json={"suggestion_id": sid, "decision": "accept"})
    assert r.status_code == 422
    assert client.get(f"/api/projects/{pid}/qual/codebook").json() == {"codes": {}}
    listed = client.get(f"/api/projects/{pid}/qual/excerpts").json()
    assert listed[0]["suggestions"][0]["status"] == "pending"


def test_the_codebook_audit_events_hold_names_not_definitions(project, monkeypatch):
    client, pid = project
    _patch(monkeypatch, FakeHttp(_reply("trust")), _settings())
    excerpt = client.post(f"/api/projects/{pid}/qual/suggest", json={"excerpt": EXCERPT}).json()
    client.post(f"/api/projects/{pid}/qual/decisions",
                json={"suggestion_id": excerpt["suggestions"][0]["id"], "decision": "accept"})
    client.post(f"/api/projects/{pid}/qual/codebook/rename", json={"old": "trust", "new": "distrust-cues"})
    with SessionLocal() as db:
        events = db.scalars(select(AuditEvent).where(AuditEvent.action.in_(
            ["qual.code_added", "qual.code_renamed"]))).all()
        assert events
        for event in events:
            assert "from the excerpt" not in str(event.payload_json)



def test_every_stored_suggestion_records_the_model_and_the_pinned_prompt_version(project, monkeypatch):
    # Integrity rule 5: an AI output records model_id and prompt_version (X.121).
    client, pid = project
    _patch(monkeypatch, FakeHttp(_reply("trust", "silence")), _settings())
    body = client.post(f"/api/projects/{pid}/qual/suggest", json={"excerpt": EXCERPT}).json()
    expected = ("qwen2.5:7b-instruct", "qual_code_suggestions@1")
    assert [(s["model_id"], s["prompt_version"]) for s in body["suggestions"]] == [expected, expected]
    with SessionLocal() as db:
        rows = db.scalars(select(QualSuggestion).where(QualSuggestion.excerpt_id == uuid.UUID(body["id"]))).all()
        assert [(r.model_id, r.prompt_version) for r in rows] == [expected, expected]
        event = db.scalars(select(AuditEvent).where(AuditEvent.action == "qual.suggested",
                                                    AuditEvent.project_id == uuid.UUID(pid))).one()
        assert (event.model_id, event.prompt_version) == expected
