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


def act(world, suggestion_id, what, **body):
    return world["owner"].post(f"/api/projects/{world['pid']}/suggestions/{suggestion_id}/{what}", json=body or None)


EXCLUDE = answer("exclude", "E1", 0.8, "An opinion piece.")
INCLUDE = answer("include", "I1", 0.9)


def test_a_prescreen_result_is_a_suggestion_that_decides_nothing_until_accepted(world):
    prescreen(world, [EXCLUDE, EXCLUDE])
    (first,) = listed(world, world["ids"][0])
    assert first["state"] == "pending" and first["content"] == "Exclude (E1): An opinion piece."
    assert first["model_id"] == "test-model" and first["confidence"] == 0.8
    assert human_rows(world, world["ids"][0]) == []  # a suggestion alone is not a decision


def test_accepting_records_the_persons_own_decision_with_the_suggested_reason(world):
    prescreen(world, [EXCLUDE, EXCLUDE])
    sid = listed(world, world["ids"][0])[0]["id"]
    assert act(world, sid, "accept").status_code == 200
    (row,) = human_rows(world, world["ids"][0])
    assert (row.decision, row.reason_code, row.decided_by, row.note) == ("exclude", "E1", "human", None)
    counts = world["owner"].get(f"/api/projects/{world['pid']}/screening/queue").json()["counts"]
    assert counts["exclude"] == 1 and counts["unscreened"] == 1


def test_edit_then_accept_keeps_the_persons_wording_as_the_note(world):
    prescreen(world, [EXCLUDE, EXCLUDE])
    sid = listed(world, world["ids"][0])[0]["id"]
    assert act(world, sid, "edit-accept", text="Editorial, no data").status_code == 200
    (row,) = human_rows(world, world["ids"][0])
    assert row.decision == "exclude" and row.note == "Editorial, no data"


def test_discarding_writes_no_decision_and_undo_appends_an_undo_row(world):
    prescreen(world, [INCLUDE, INCLUDE])
    first, second = (listed(world, i)[0]["id"] for i in world["ids"])
    assert act(world, first, "discard").status_code == 200
    assert human_rows(world, world["ids"][0]) == []
    assert act(world, second, "accept").status_code == 200
    assert act(world, second, "undo").status_code == 200
    assert [r.decision for r in human_rows(world, world["ids"][1])] == ["include", "undo"]  # history kept, source back in the queue
    queue = world["owner"].get(f"/api/projects/{world['pid']}/screening/queue").json()
    assert world["ids"][1] in {i["source_id"] for i in queue["items"]}


def test_accepting_is_refused_once_g3_is_approved_and_nothing_is_written(world):
    prescreen(world, [INCLUDE, INCLUDE])
    sid = listed(world, world["ids"][0])[0]["id"]
    with SessionLocal() as db:
        gate = db.scalar(select(Gate).where(Gate.project_id == uuid.UUID(world["pid"]), Gate.code == GateCode.G3))
        if gate is None:
            gate = Gate(project_id=uuid.UUID(world["pid"]), code=GateCode.G3)
            db.add(gate)
        gate.status = GateStatus.approved
        db.commit()
    response = act(world, sid, "accept")
    assert response.status_code == 422 and "G3" in response.json()["detail"]
    assert human_rows(world, world["ids"][0]) == []
    assert listed(world, world["ids"][0])[0]["state"] == "pending"


def test_dual_screening_hides_screening_suggestions_and_refuses_to_accept_them(world):
    prescreen(world, [INCLUDE, INCLUDE])
    sid = listed(world, world["ids"][0])[0]["id"]
    world["owner"].put(f"/api/projects/{world['pid']}/screening/dual-screen", json={"enabled": True})
    assert listed(world, world["ids"][0]) == []
    assert world["owner"].get(f"/api/projects/{world['pid']}/suggestions/{sid}/why").status_code == 404
    assert act(world, sid, "accept").status_code == 422
    assert human_rows(world, world["ids"][0]) == []
