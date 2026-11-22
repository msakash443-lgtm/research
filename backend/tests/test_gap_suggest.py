"""AI-suggested gaps and research questions are checked by code and never create anything (plan X.192)."""
import json
import uuid

import pytest

from app.database import SessionLocal
from app.models import Extraction
from tests.api_helpers import create_project, insert_source, sign_in

SYSTEM_BAD_ID = str(uuid.uuid4())


def _reply(key, items):
    return {"choices": [{"message": {"content": json.dumps({key: items})}}]}


@pytest.fixture
def world(fake_llm):
    client = sign_in(f"gs-{uuid.uuid4().hex}@example.com")
    pid = create_project(client, "Remote work")
    ids = []
    for title, method, population, flags in (("Paper A", "survey", "teachers", None), ("Paper B", "interview", "nurses", None),
                                             ("Retracted C", "survey", "nurses", ["retracted"])):
        sid = insert_source(pid, title=title, year=2020, abstract=f"{title} abstract", metadata_verified=True, quality_flags=flags)
        ids.append(sid)
        with SessionLocal() as db:
            db.add(Extraction(project_id=uuid.UUID(pid), source_id=uuid.UUID(sid), schema_version="default@1",
                              fields_json={"method": {"design": method}, "sample": {"population": population, "country": "UK"}},
                              evidence_spans={}, extracted_by="human", extractor="test"))
            db.commit()
    return client, pid, ids, fake_llm


def _gap(ids, **overrides):
    return {"type": "empirical", "statement": "No study links survey and interview findings.", "source_ids": ids[:2],
            "reason": "A and B use different methods.", **overrides}


def test_a_valid_gap_suggestion_is_stored_as_a_pending_card_and_creates_no_gap(world):
    client, pid, ids, llm = world
    llm.handler = lambda r: _reply("gaps", [_gap(ids)])
    response = client.post(f"/api/projects/{pid}/gaps/suggest")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["rejected"] == [] and body["prompt_version"] == "gap_suggestions@1" and body["model_id"] == "fake-model"
    assert body["proposals"][0]["source_ids"] == ids[:2]
    cards = client.get(f"/api/projects/{pid}/suggestions", params={"target_kind": "gap"}).json()
    assert [c["id"] for c in cards] == [str(body["proposals"][0]["suggestion_id"])]
    assert client.get(f"/api/projects/{pid}/gaps").json() == []
    assert any(e["action"] == "gap.suggested" for e in client.get(f"/api/projects/{pid}/audit").json())


def test_a_gap_citing_an_invented_unverified_or_single_source_is_rejected_loudly_and_not_stored(world):
    client, pid, ids, llm = world
    llm.handler = lambda r: _reply("gaps", [
        _gap(ids, source_ids=[ids[0], SYSTEM_BAD_ID], statement="Invented source id."),
        _gap(ids, source_ids=[ids[0], ids[2]], statement="Retracted source cited."),
        _gap(ids, source_ids=[ids[0], ids[0]], statement="Same source twice."),
        _gap(ids, type="vibes", statement="Bad type."),
    ])
    body = client.post(f"/api/projects/{pid}/gaps/suggest").json()
    assert body["proposals"] == []
    assert [r["summary"] for r in body["rejected"]] == ["Invented source id.", "Retracted source cited.", "Same source twice.", "Bad type."]
    assert "not one of the verified" in body["rejected"][0]["reason"] and "at least 2" in body["rejected"][2]["reason"]
    assert client.get(f"/api/projects/{pid}/suggestions", params={"target_kind": "gap"}).json() == []
    assert str(ids[2]) not in json.dumps(llm.requests[0].content.decode()), "a retracted source must not be shown to the model"


def test_source_text_is_fenced_in_the_gap_request(world):
    client, pid, ids, llm = world
    llm.handler = lambda r: _reply("gaps", [])
    client.post(f"/api/projects/{pid}/gaps/suggest")
    prompt = json.loads(llm.requests[0].content)["messages"][-1]["content"]
    assert f"<<<SOURCE id={ids[0]} fence=" in prompt and "Gap candidates found by code" in prompt


def test_a_question_suggestion_needs_one_verified_source_and_a_single_question_mark(world):
    client, pid, ids, llm = world
    llm.handler = lambda r: _reply("questions", [
        {"text": "How do survey and interview findings differ?", "type": "Comparative", "source_ids": ids[:1], "reason": "A vs B."},
        {"text": "Not a question.", "type": "descriptive", "source_ids": ids[:1], "reason": "r"},
        {"text": "Does it matter?", "type": "causal", "source_ids": [SYSTEM_BAD_ID], "reason": "r"},
    ])
    body = client.post(f"/api/projects/{pid}/research-questions/suggest").json()
    assert [p["text"] for p in body["proposals"]] == ["How do survey and interview findings differ?"]
    assert body["proposals"][0]["type"] == "comparative" and body["prompt_version"] == "rq_suggestions@1"
    assert len(body["rejected"]) == 2
    assert client.get(f"/api/projects/{pid}/research-questions").json() == []


def test_with_ai_off_the_suggest_routes_answer_409_and_never_call_the_model(world):
    client, pid, ids, llm = world
    assert client.put(f"/api/projects/{pid}/ai-settings", json={"ai_involvement": "off"}).status_code == 200
    assert client.post(f"/api/projects/{pid}/gaps/suggest").status_code == 409
    assert client.post(f"/api/projects/{pid}/research-questions/suggest").status_code == 409
    assert llm.requests == []


def test_a_project_with_no_candidates_answers_409_without_calling_the_model(fake_llm):
    client = sign_in(f"gs-{uuid.uuid4().hex}@example.com")
    pid = create_project(client, "Empty")
    assert client.post(f"/api/projects/{pid}/gaps/suggest").status_code == 409
    assert fake_llm.requests == []
