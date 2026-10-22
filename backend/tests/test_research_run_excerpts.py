"""X.107: a research run shows every excerpt of a source, and each claim is linked to the excerpt its text came from."""
import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models import Claim, ClaimEvidence, SourceExcerpt, Task, TaskStatus
from tests.llm_replies import chat_reply

WAGES = "Wages fell three percent in 2020 across the surveyed firms."
EMPLOYMENT = "Employment rose five percent in 2021 after the reopening."


def _project_with_two_excerpts():
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": "excerpts-x107@example.com", "display_name": "E"})
    pid = client.post("/api/projects", json={"title": "Excerpts"}).json()["id"]
    sid = client.post(
        f"/api/projects/{pid}/sources",
        json={"title": "Labour report", "evidence_excerpt": WAGES, "locator": "p. 2"},
    ).json()["id"]
    client.post(f"/api/projects/{pid}/sources/{sid}/excerpts", json={"content": EMPLOYMENT, "locator": "p. 9"})
    client.post(f"/api/projects/{pid}/sources/{sid}/verify")
    return client, pid


def test_the_prompt_shows_every_excerpt_of_a_source(fake_llm):
    fake_llm.handler = lambda request: chat_reply("Wages fell [S1]. Employment rose [S1].")
    client, pid = _project_with_two_excerpts()

    run = client.post(f"/api/projects/{pid}/research-runs", json={"question": "What changed in the labour market?"})

    assert run.status_code == 202
    user = json.loads(fake_llm.requests[0].content)["messages"][1]["content"]
    assert WAGES in user and EMPLOYMENT in user


def test_each_claim_is_linked_to_the_excerpt_its_text_came_from(fake_llm):
    fake_llm.handler = lambda request: chat_reply(
        "Wages fell three percent in 2020 [S1]. Employment rose five percent in 2021 [S1]."
    )
    client, pid = _project_with_two_excerpts()

    client.post(f"/api/projects/{pid}/research-runs", json={"question": "What changed in the labour market?"})

    with SessionLocal() as db:
        linked = {evidence.quote: evidence.excerpt_id for evidence in db.scalars(select(ClaimEvidence))}
        ids = {excerpt.content: excerpt.id for excerpt in db.scalars(select(SourceExcerpt))}
    assert linked == {WAGES: ids[WAGES], EMPLOYMENT: ids[EMPLOYMENT]}


def test_excerpts_reach_the_prompt_oldest_first(fake_llm):
    fake_llm.handler = lambda request: chat_reply("Wages fell [S1].")
    client, pid = _project_with_two_excerpts()

    client.post(f"/api/projects/{pid}/research-runs", json={"question": "What changed in the labour market?"})

    user = json.loads(fake_llm.requests[0].content)["messages"][1]["content"]
    assert user.index(WAGES) < user.index(EMPLOYMENT)


def test_a_claim_shared_equally_with_two_excerpts_fails_the_run_instead_of_guessing(fake_llm):
    fake_llm.handler = lambda request: chat_reply("Wages and employment changed [S1].")  # one word from each excerpt
    client, pid = _project_with_two_excerpts()

    run = client.post(f"/api/projects/{pid}/research-runs", json={"question": "What changed in the labour market?"}).json()

    assert run["status"] == "failed" and "could not be tied to one excerpt" in run["error_message"]
    with SessionLocal() as db:
        assert db.scalars(select(Claim)).all() == [] and db.scalars(select(ClaimEvidence)).all() == []
        # A rejected answer is not retried: asking the model again would give the same answer (X.106).
        assert db.scalars(select(Task)).one().status == TaskStatus.completed
