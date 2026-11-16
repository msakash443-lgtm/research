import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models import Gate, GateCode, GateStatus, Project, ScreeningDecision
from app.prescreen import run_prescreen
from tests.test_prescreen import ScriptedLLM, answer


@pytest.fixture
def world():
    tag = uuid.uuid4().hex[:8]
    owner = TestClient(app)
    owner.post("/api/auth/development/login", json={"email": f"ss-{tag}@example.com", "display_name": "S"})
    pid = owner.post("/api/projects", json={"title": "Screening suggestions"}).json()["id"]
    owner.put(
        f"/api/projects/{pid}/criteria",
        json={"framework": "custom", "criteria": [{"kind": "include", "text": "Empirical studies"}, {"kind": "exclude", "text": "Opinion pieces"}]},
    )
    ids = [owner.post(f"/api/projects/{pid}/sources", json={"title": f"Paper {n}"}).json()["id"] for n in range(2)]
    return {"owner": owner, "pid": pid, "ids": ids}


def prescreen(world, replies):
    with SessionLocal() as db:
        project = db.get(Project, uuid.UUID(world["pid"]))
        run_prescreen(db, project, llm=ScriptedLLM(replies), min_confidence=0.6, actor="tester")


def listed(world, source_id, **params):
    return world["owner"].get(f"/api/projects/{world['pid']}/suggestions", params={"target_kind": "screening", "target_id": source_id, **params}).json()


def human_rows(world, source_id):
    with SessionLocal() as db:
        return list(db.scalars(select(ScreeningDecision).where(ScreeningDecision.source_id == uuid.UUID(source_id), ScreeningDecision.decided_by == "human").order_by(ScreeningDecision.seq)))


def decide_myself(world, source_id, decision, reason_code=None):
    body = {"source_id": source_id, "decision": decision, "reason_code": reason_code}
    return world["owner"].post(f"/api/projects/{world['pid']}/screening/decisions", json=body)


def suggestion_id_after_deciding(world, source_id, decision, reason_code=None):
    assert decide_myself(world, source_id, decision, reason_code).status_code == 201
    return listed(world, source_id)[0]["id"]


def act(world, suggestion_id, what, **body):
    return world["owner"].post(f"/api/projects/{world['pid']}/suggestions/{suggestion_id}/{what}", json=body or None)


EXCLUDE = answer("exclude", "E1", 0.8, "An opinion piece.")
INCLUDE = answer("include", "I1", 0.9)


def test_a_prescreen_result_is_withheld_until_the_person_decides_and_then_decides_nothing_itself(world):
    prescreen(world, [EXCLUDE, EXCLUDE])
    first, base = world["ids"][0], f"/api/projects/{world['pid']}"
    sid = next(s.id for s in _stored(world, first))
    assert listed(world, first) == []  # not before my own decision
    assert world["owner"].get(f"{base}/suggestions/{sid}/why").status_code == 404
    assert act(world, sid, "accept").status_code == 404 and act(world, sid, "discard").status_code == 404
    assert world["owner"].get(f"{base}/screening/suggestion/{first}").status_code == 404
    assert all("ai_suggestion" not in item for item in world["owner"].get(f"{base}/screening/queue").json()["items"])
    assert [r["decided_by"] for r in world["owner"].get(f"{base}/screening/decisions", params={"source_id": first}).json()] == []
    assert decide_myself(world, first, "include", "I1").status_code == 201
    (shown,) = listed(world, first)
    assert shown["state"] == "pending" and shown["content"] == "Exclude (E1): An opinion piece."
    assert shown["model_id"] == "test-model" and shown["confidence"] == 0.8
    assert [r.decision for r in human_rows(world, first)] == ["include"]  # the suggestion changed nothing
    assert world["owner"].get(f"{base}/suggestions/{shown['id']}/why").status_code == 200
    revealed = world["owner"].get(f"{base}/screening/suggestion/{first}").json()
    assert revealed["your_decision"] == "include" and revealed["agrees"] is False
    assert revealed["suggestion"]["decision"] == "exclude" and revealed["suggestion"]["reason_code"] == "E1"
    history = world["owner"].get(f"{base}/screening/decisions", params={"source_id": first}).json()
    assert {r["decided_by"] for r in history} == {"ai", "human"}  # the AI row appears in the history only now


def _stored(world, source_id):
    from app.models import AiSuggestion

    with SessionLocal() as db:
        return list(db.scalars(select(AiSuggestion).where(AiSuggestion.target_id == source_id)))


def test_changing_my_decision_to_the_suggestion_records_my_own_decision_with_its_reason(world):
    prescreen(world, [EXCLUDE, EXCLUDE])
    sid = suggestion_id_after_deciding(world, world["ids"][0], "include", "I1")
    assert act(world, sid, "accept").status_code == 200
    first, last = human_rows(world, world["ids"][0])
    assert (first.decision, last.decision, last.reason_code, last.decided_by, last.note) == ("include", "exclude", "E1", "human", None)
    counts = world["owner"].get(f"/api/projects/{world['pid']}/screening/queue").json()["counts"]
    assert counts["exclude"] == 1 and counts["unscreened"] == 1


def test_accepting_a_suggestion_that_matches_my_decision_is_refused(world):
    prescreen(world, [EXCLUDE, EXCLUDE])
    sid = suggestion_id_after_deciding(world, world["ids"][0], "exclude", "E1")
    response = act(world, sid, "accept")
    assert response.status_code == 422 and "already matches" in response.json()["detail"]
    assert len(human_rows(world, world["ids"][0])) == 1


def test_edit_then_accept_keeps_the_persons_wording_as_the_note(world):
    prescreen(world, [EXCLUDE, EXCLUDE])
    sid = suggestion_id_after_deciding(world, world["ids"][0], "include", "I1")
    assert act(world, sid, "edit-accept", text="Editorial, no data").status_code == 200
    last = human_rows(world, world["ids"][0])[-1]
    assert last.decision == "exclude" and last.note == "Editorial, no data"


def test_discarding_writes_no_decision_and_undo_appends_an_undo_row(world):
    prescreen(world, [INCLUDE, EXCLUDE])
    first = suggestion_id_after_deciding(world, world["ids"][0], "maybe")
    second = suggestion_id_after_deciding(world, world["ids"][1], "include", "I1")
    assert act(world, first, "discard").status_code == 200
    assert [r.decision for r in human_rows(world, world["ids"][0])] == ["maybe"]
    assert act(world, second, "accept").status_code == 200
    assert act(world, second, "undo").status_code == 200
    assert [r.decision for r in human_rows(world, world["ids"][1])] == ["include", "exclude", "undo"]  # history kept
    queue = world["owner"].get(f"/api/projects/{world['pid']}/screening/queue").json()
    assert world["ids"][1] in {i["source_id"] for i in queue["items"]}
    assert listed(world, world["ids"][1]) == []  # back in the queue, undecided: the suggestion is withheld again


def test_bulk_never_accepts_or_discards_suggestions_for_sources_i_have_not_decided(world):
    prescreen(world, [INCLUDE, EXCLUDE])
    decided = suggestion_id_after_deciding(world, world["ids"][0], "maybe")
    response = world["owner"].post(f"/api/projects/{world['pid']}/suggestions/bulk", json={"accept_at_or_above": 0.5, "target_kind": "screening"})
    assert response.status_code == 200
    touched = {*response.json()["accepted"], *response.json()["discarded"], *response.json()["skipped"]}
    assert touched <= {decided}
    assert human_rows(world, world["ids"][1]) == []


def test_accepting_is_refused_once_g3_is_approved_and_nothing_is_written(world):
    prescreen(world, [INCLUDE, INCLUDE])
    sid = suggestion_id_after_deciding(world, world["ids"][0], "maybe")
    with SessionLocal() as db:
        gate = db.scalar(select(Gate).where(Gate.project_id == uuid.UUID(world["pid"]), Gate.code == GateCode.G3))
        if gate is None:
            gate = Gate(project_id=uuid.UUID(world["pid"]), code=GateCode.G3)
            db.add(gate)
        gate.status = GateStatus.approved
        db.commit()
    response = act(world, sid, "accept")
    assert response.status_code == 422 and "G3" in response.json()["detail"]
    assert [r.decision for r in human_rows(world, world["ids"][0])] == ["maybe"]
    assert listed(world, world["ids"][0])[0]["state"] == "pending"


def test_dual_screening_withholds_the_suggestion_until_my_own_decision_too(world):
    prescreen(world, [INCLUDE, INCLUDE])
    world["owner"].put(f"/api/projects/{world['pid']}/screening/dual-screen", json={"enabled": True})
    sid = next(s.id for s in _stored(world, world["ids"][0]))
    assert listed(world, world["ids"][0]) == []
    assert world["owner"].get(f"/api/projects/{world['pid']}/suggestions/{sid}/why").status_code == 404
    assert act(world, sid, "accept").status_code == 404
    assert human_rows(world, world["ids"][0]) == []
    assert decide_myself(world, world["ids"][0], "maybe").status_code == 201
    assert listed(world, world["ids"][0])[0]["id"] == str(sid)
