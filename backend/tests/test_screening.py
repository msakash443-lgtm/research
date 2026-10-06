import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app.database import SessionLocal
from app.main import app
from app.models import AuditEvent, Gate, GateCode, GateStatus, ProjectMember, ProjectRole, ScreeningDecision, Source


def _login(email):
    client = TestClient(app)
    user_id = client.post("/api/auth/development/login", json={"email": email, "display_name": "S"}).json()["id"]
    return client, user_id


@pytest.fixture
def world():
    tag = uuid.uuid4().hex[:8]
    owner, owner_id = _login(f"screen-owner-{tag}@example.com")
    pid = owner.post("/api/projects", json={"title": "Screening"}).json()["id"]
    others = {}
    for role in ("co_author", "supervisor", "reviewer"):
        client, uid = _login(f"screen-{role}-{tag}@example.com")
        with SessionLocal() as db:
            db.add(ProjectMember(project_id=uuid.UUID(pid), user_id=uuid.UUID(uid), role=ProjectRole(role)))
            db.commit()
        others[role] = client
    owner.put(
        f"/api/projects/{pid}/criteria",
        json={"framework": "custom", "criteria": [
            {"kind": "include", "text": "Empirical studies"},
            {"kind": "exclude", "text": "Opinion pieces"},
            {"kind": "exclude", "text": "Not in English"},
        ]},
    )
    ids = [owner.post(f"/api/projects/{pid}/sources", json={"title": f"Paper {n}"}).json()["id"] for n in range(3)]
    return {"owner": owner, "owner_id": owner_id, "pid": pid, "ids": ids, **others}


def decide(client, pid, source_id, decision="include", **extra):
    return client.post(f"/api/projects/{pid}/screening/decisions", json={"source_id": source_id, "decision": decision, **extra})


def undo(client, pid, source_id, stage="title_abstract"):
    return client.post(f"/api/projects/{pid}/screening/decisions/undo", json={"source_id": source_id, "stage": stage})


def queue(client, pid, stage="title_abstract"):
    return client.get(f"/api/projects/{pid}/screening/queue", params={"stage": stage}).json()


def test_queue_lists_every_unscreened_source_with_counts(world):
    data = queue(world["owner"], world["pid"])
    assert data["counts"] == {"include": 0, "exclude": 0, "maybe": 0, "unscreened": 3}
    assert {i["title"] for i in data["items"]} == {"Paper 0", "Paper 1", "Paper 2"}
    assert data["locked"] is False


def test_a_decision_takes_the_source_out_of_the_queue(world):
    r = decide(world["owner"], world["pid"], world["ids"][0], "include", reason_code="I1")
    assert r.status_code == 201
    assert r.json()["decided_by"] == "human" and r.json()["decider"] == world["owner_id"] and r.json()["seq"] == 1
    data = queue(world["owner"], world["pid"])
    assert data["counts"]["include"] == 1 and data["counts"]["unscreened"] == 2
    assert world["ids"][0] not in {i["source_id"] for i in data["items"]}


def test_an_exclusion_must_cite_one_of_the_projects_exclusion_codes(world):
    p, s = world["pid"], world["ids"][0]
    assert decide(world["owner"], p, s, "exclude").status_code == 422  # no reason
    assert decide(world["owner"], p, s, "exclude", reason_code="I1").status_code == 422  # an inclusion code
    assert decide(world["owner"], p, s, "exclude", reason_code="E9").status_code == 422  # not a criterion
    assert decide(world["owner"], p, s, "include", reason_code="E1").status_code == 422
    assert decide(world["owner"], p, s, "exclude", reason_code="E2").status_code == 201
    assert queue(world["owner"], p)["counts"]["exclude"] == 1


def test_maybe_and_include_need_no_reason(world):
    assert decide(world["owner"], world["pid"], world["ids"][0], "maybe").status_code == 201
    assert decide(world["owner"], world["pid"], world["ids"][1], "include").status_code == 201


def test_changing_your_mind_appends_and_the_latest_wins(world):
    p, s = world["pid"], world["ids"][0]
    decide(world["owner"], p, s, "include")
    second = decide(world["owner"], p, s, "exclude", reason_code="E1").json()
    assert second["seq"] == 2
    counts = queue(world["owner"], p)["counts"]
    assert counts["exclude"] == 1 and counts["include"] == 0
    history = world["owner"].get(f"/api/projects/{p}/screening/decisions", params={"source_id": s}).json()
    assert [h["decision"] for h in history] == ["exclude", "include"]  # newest first, both kept


def test_undo_returns_the_source_to_the_queue_and_keeps_the_history(world):
    p, s = world["pid"], world["ids"][0]
    decide(world["owner"], p, s, "include")
    assert undo(world["owner"], p, s).status_code == 201
    data = queue(world["owner"], p)
    assert data["counts"]["unscreened"] == 3 and s in {i["source_id"] for i in data["items"]}
    history = world["owner"].get(f"/api/projects/{p}/screening/decisions", params={"source_id": s}).json()
    assert [h["decision"] for h in history] == ["undo", "include"]


def test_undo_without_a_decision_is_refused(world):
    assert undo(world["owner"], world["pid"], world["ids"][0]).status_code == 409


def test_an_ai_suggestion_is_shown_but_never_counts_as_a_decision(world):
    p, s = world["pid"], world["ids"][0]
    with SessionLocal() as db:
        db.add(ScreeningDecision(
            project_id=uuid.UUID(p), source_id=uuid.UUID(s), stage="title_abstract", seq=1, decision="exclude",
            reason_code="E1", decided_by="ai", decider="agent:screener", confidence=0.9,
        ))
        db.commit()
    data = queue(world["owner"], p)
    item = next(i for i in data["items"] if i["source_id"] == s)
    assert item["ai_suggestion"] == {"decision": "exclude", "reason_code": "E1", "confidence": 0.9, "rationale": None}
    assert data["counts"]["exclude"] == 0 and data["counts"]["unscreened"] == 3  # still waiting for a person
    # a person then decides; the AI row stays in the history and a person's decision is what counts
    assert decide(world["owner"], p, s, "include").json()["seq"] == 2
    counts = queue(world["owner"], p)["counts"]
    assert counts["include"] == 1 and counts["exclude"] == 0


def test_full_text_only_takes_sources_a_person_passed_at_title_abstract(world):
    p, ids = world["pid"], world["ids"]
    assert decide(world["owner"], p, ids[0], "include", stage="full_text").status_code == 409  # not screened yet
    decide(world["owner"], p, ids[0], "include")
    decide(world["owner"], p, ids[1], "maybe")
    decide(world["owner"], p, ids[2], "exclude", reason_code="E1")
    assert {i["source_id"] for i in queue(world["owner"], p, "full_text")["items"]} == {ids[0], ids[1]}
    assert decide(world["owner"], p, ids[2], "include", stage="full_text").status_code == 409
    assert decide(world["owner"], p, ids[0], "exclude", reason_code="E2", stage="full_text").status_code == 201


def test_a_source_with_a_full_text_decision_cant_be_dropped_at_title_abstract(world):
    p, s = world["pid"], world["ids"][0]
    decide(world["owner"], p, s, "include")
    decide(world["owner"], p, s, "include", stage="full_text")
    assert decide(world["owner"], p, s, "exclude", reason_code="E1").status_code == 409
    assert undo(world["owner"], p, s).status_code == 409
    assert undo(world["owner"], p, s, "full_text").status_code == 201
    assert undo(world["owner"], p, s).status_code == 201


def test_g3_approval_locks_screening_until_it_is_reopened(world):
    p, s = world["pid"], world["ids"][0]
    decide(world["owner"], p, s, "include")
    with SessionLocal() as db:
        db.execute(text("select 1"))
        gate = db.scalar(select(Gate).where(Gate.project_id == uuid.UUID(p), Gate.code == GateCode.G3))
        if gate is None:
            gate = Gate(project_id=uuid.UUID(p), code=GateCode.G3)
            db.add(gate)
        gate.status = GateStatus.approved
        db.commit()
    assert queue(world["owner"], p)["locked"] is True
    assert decide(world["owner"], p, world["ids"][1], "include").status_code == 409
    assert undo(world["owner"], p, s).status_code == 409
    assert queue(world["owner"], p)["counts"]["include"] == 1  # nothing changed


def test_only_owner_and_co_author_decide_everyone_reads(world):
    p, s = world["pid"], world["ids"][0]
    assert decide(world["reviewer"], p, s).status_code == 403
    assert decide(world["supervisor"], p, s).status_code == 403
    assert undo(world["reviewer"], p, s).status_code == 403
    assert world["reviewer"].get(f"/api/projects/{p}/screening/queue").status_code == 200
    assert decide(world["co_author"], p, s).status_code == 201
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ScreeningDecision).where(ScreeningDecision.project_id == uuid.UUID(p))) == 1


def test_a_source_from_another_project_or_a_merged_one_is_not_found(world):
    other, _ = _login(f"screen-other-{uuid.uuid4().hex[:6]}@example.com")
    other_pid = other.post("/api/projects", json={"title": "Other"}).json()["id"]
    foreign = other.post(f"/api/projects/{other_pid}/sources", json={"title": "Elsewhere"}).json()["id"]
    assert decide(world["owner"], world["pid"], foreign).status_code == 404
    merged = world["ids"][2]
    with SessionLocal() as db:
        db.get(Source, uuid.UUID(merged)).merged_into = uuid.UUID(world["ids"][0])
        db.commit()
    assert decide(world["owner"], world["pid"], merged).status_code == 404
    assert merged not in {i["source_id"] for i in queue(world["owner"], world["pid"])["items"]}


def test_bad_input_is_refused(world):
    p, s = world["pid"], world["ids"][0]
    assert decide(world["owner"], p, s, "undo").status_code == 422  # undo has its own route
    assert decide(world["owner"], p, s, "include", stage="abstract").status_code == 422
    assert world["owner"].post(f"/api/projects/{p}/screening/decisions", json={"source_id": s, "decision": "include", "decided_by": "ai"}).status_code == 422
    assert world["owner"].get(f"/api/projects/{p}/screening/queue", params={"stage": "x"}).status_code == 422


def test_decisions_are_audited_without_the_note(world):
    p, s = world["pid"], world["ids"][0]
    decide(world["owner"], p, s, "include", note="private reasoning")
    undo(world["owner"], p, s)
    events = world["owner"].get(f"/api/projects/{p}/audit").json()
    actions = {e["action"] for e in events}
    assert {"screening.decided", "screening.undone"} <= actions
    assert "private reasoning" not in str(events)


def test_refusals_write_nothing(world):
    p, s = world["pid"], world["ids"][0]
    decide(world["owner"], p, s, "exclude")  # 422
    undo(world["owner"], p, s)  # 409
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ScreeningDecision).where(ScreeningDecision.project_id == uuid.UUID(p))) == 0
        assert db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.project_id == uuid.UUID(p), AuditEvent.action.like("screening.%"))) == 0


def test_rows_cannot_be_updated_or_deleted(world):
    p, s = world["pid"], world["ids"][0]
    decide(world["owner"], p, s, "include")
    with SessionLocal() as db:
        row = db.scalar(select(ScreeningDecision).where(ScreeningDecision.project_id == uuid.UUID(p)))
        row.decision = "exclude"
        with pytest.raises(ValueError, match="append-only"):
            db.commit()
        db.rollback()
        db.delete(db.scalar(select(ScreeningDecision).where(ScreeningDecision.project_id == uuid.UUID(p))))
        with pytest.raises(ValueError, match="append-only"):
            db.commit()
