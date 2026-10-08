"""LLM synonym proposals are validated, filtered by code, audited, and never saved (plan M1.5.3)."""
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.search_query import ConceptBlock
from app.synonym_suggest import filter_proposals

BLOCK = ConceptBlock(label="work", terms=("remote work", "telework"))


def _reply(*pairs):
    content = json.dumps({"suggestions": [{"term": t, "reason": r} for t, r in pairs]})
    return {"choices": [{"message": {"content": content}}]}


def test_filter_drops_terms_already_in_use_duplicates_and_unusable_ones():
    reply = {"suggestions": [
        {"term": "Remote Work", "reason": "same as existing"},
        {"term": "working from home", "reason": "common phrase"},
        {"term": "working  from   home", "reason": "dup"},
        {"term": "telecommuting*", "reason": "wildcard"},
        {"term": "a b c d e", "reason": "too long"},
        {"term": "AND", "reason": "operator"},
        {"term": "WFH", "reason": ""},
        {"term": "distributed work", "reason": "x" * 1000},
    ]}
    kept, dropped = filter_proposals(BLOCK, reply)
    assert [p.term for p in kept] == ["working from home", "distributed work"]
    assert len(kept[1].reason) == 300 and dropped == 6


def test_filter_caps_the_number_of_proposals():
    reply = {"suggestions": [{"term": f"term {i}", "reason": "r"} for i in range(30)]}
    assert len(filter_proposals(BLOCK, reply)[0]) == 12


@pytest.fixture
def project(fake_llm):
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": "syn@example.com", "display_name": "S"})
    return client, client.post("/api/projects", json={"title": "Remote work"}).json()["id"]


def test_proposals_come_back_filtered_with_provenance_and_nothing_is_saved(project, fake_llm):
    client, pid = project
    fake_llm.handler = lambda r: _reply(("telework", "already used"), ("working from home", "common phrase"))
    body = client.post(f"/api/projects/{pid}/searches/suggest-synonyms", json={"block": {"label": "work", "terms": ["remote work", "telework"]}}).json()
    assert [p["term"] for p in body["proposals"]] == ["working from home"] and body["dropped"] == 1
    assert body["model_id"] == "fake-model" and body["prompt_version"] == "synonym_suggestions@1"
    assert client.get(f"/api/projects/{pid}/searches").json()["searches"] == []
    events = client.get(f"/api/projects/{pid}/audit").json()
    suggested = [e for e in events if e["action"] == "search.synonyms_suggested"]
    assert len(suggested) == 1 and suggested[0]["model_id"] == "fake-model" and "working from home" not in json.dumps(suggested)


def test_a_reply_in_the_wrong_shape_fails_loudly_with_no_proposals(project, fake_llm):
    client, pid = project
    fake_llm.handler = lambda r: {"choices": [{"message": {"content": "Try telecommuting and WFH."}}]}
    response = client.post(f"/api/projects/{pid}/searches/suggest-synonyms", json={"block": {"terms": ["remote work"]}})
    assert response.status_code == 502 and "proposals" not in response.json()


def test_without_a_model_the_request_says_so(project, monkeypatch):
    from app.config import get_settings

    client, pid = project
    monkeypatch.setattr(get_settings(), "llm_api_key", None)
    response = client.post(f"/api/projects/{pid}/searches/suggest-synonyms", json={"block": {"terms": ["remote work"]}})
    assert response.status_code == 503


def test_an_unusable_block_is_refused_before_any_model_call(project, fake_llm):
    client, pid = project
    assert client.post(f"/api/projects/{pid}/searches/suggest-synonyms", json={"block": {"terms": ["AND"]}}).status_code == 422
    assert fake_llm.requests == []
