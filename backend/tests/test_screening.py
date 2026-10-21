import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app.database import SessionLocal
from app.main import app
from app.models import AuditEvent, AutoExcludeAudit, Gate, GateCode, GateStatus, ProjectMember, ProjectRole, ScreeningDecision, Source


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


def set_dual_screen(client, pid, enabled=True):
    return client.put(f"/api/projects/{pid}/screening/dual-screen", json={"enabled": enabled})


def set_auto_exclude(client, pid, enabled=True, confidence_threshold=None):
    return client.put(f"/api/projects/{pid}/screening/auto-exclude", json={"enabled": enabled, "confidence_threshold": confidence_threshold})


def insert_ai_decision(pid, source_id, decision="exclude", confidence=0.95, reason_code="E1", stage="title_abstract"):
    with SessionLocal() as db:
        row = ScreeningDecision(
            project_id=uuid.UUID(pid), source_id=uuid.UUID(source_id), stage=stage, seq=1, decision=decision,
            reason_code=reason_code, decided_by="ai", decider="agent:prescreen", confidence=confidence,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id


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
    assert item["ai_suggestion"] == {"decision": "exclude", "reason_code": "E1", "confidence": 0.9, "rationale": None, "confidence_level": "high"}
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


def test_the_full_text_queue_shows_whether_the_pdf_was_fetched(world):
    p, ids = world["pid"], world["ids"]
    decide(world["owner"], p, ids[0], "include")
    with SessionLocal() as db:
        db.get(Source, uuid.UUID(ids[0])).fulltext_access = {"status": "stored", "via": "manual_upload"}
        db.commit()
    full_text_item = next(i for i in queue(world["owner"], p, "full_text")["items"] if i["source_id"] == ids[0])
    assert full_text_item["fulltext_access"] == {"status": "stored", "via": "manual_upload"}
    # title/abstract items never carry it, even for an undecided source with the same stored status
    with SessionLocal() as db:
        db.get(Source, uuid.UUID(ids[1])).fulltext_access = {"status": "stored", "via": "manual_upload"}
        db.commit()
    unscreened_item = next(i for i in queue(world["owner"], p, "title_abstract")["items"] if i["source_id"] == ids[1])
    assert unscreened_item["fulltext_access"] is None


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


def test_dual_screen_mode_is_off_by_default_and_owner_only_to_change(world):
    p = world["pid"]
    assert world["owner"].get(f"/api/projects/{p}/screening/dual-screen").json() == {"enabled": False}
    assert set_dual_screen(world["co_author"], p).status_code == 403
    r = set_dual_screen(world["owner"], p, True)
    assert r.status_code == 200 and r.json() == {"enabled": True}
    assert world["owner"].get(f"/api/projects/{p}/screening/dual-screen").json() == {"enabled": True}
    actions = {e["action"] for e in world["owner"].get(f"/api/projects/{p}/audit").json()}
    assert "screening.dual_screen_changed" in actions


def test_dual_screen_keeps_a_source_in_each_reviewers_queue_until_they_personally_decide(world):
    p, s = world["pid"], world["ids"][0]
    set_dual_screen(world["owner"], p, True)
    assert decide(world["owner"], p, s, "include").status_code == 201
    # the owner is done with it: gone from their own queue; nobody's final answer yet (only one reviewer so far)
    assert s not in {i["source_id"] for i in queue(world["owner"], p)["items"]}
    counts = queue(world["owner"], p)["counts"]
    assert counts == {"include": 0, "exclude": 0, "maybe": 0, "unscreened": 3, "conflict": 0}
    # the co-author hasn't decided yet: it is still in THEIR queue
    co_items = queue(world["co_author"], p)["items"]
    assert s in {i["source_id"] for i in co_items}
    # blinded: the co-author can't see the owner's decision, through the queue or the history
    assert next(i for i in co_items if i["source_id"] == s)["ai_suggestion"] is None
    assert world["co_author"].get(f"/api/projects/{p}/screening/decisions", params={"source_id": s}).json() == []
    # but the owner can still see their own row
    own_history = world["owner"].get(f"/api/projects/{p}/screening/decisions", params={"source_id": s}).json()
    assert [h["decision"] for h in own_history] == ["include"]


def test_dual_screen_withholds_the_ai_suggestion_until_you_decide(world):
    p, s = world["pid"], world["ids"][0]
    set_dual_screen(world["owner"], p, True)
    with SessionLocal() as db:
        db.add(ScreeningDecision(
            project_id=uuid.UUID(p), source_id=uuid.UUID(s), stage="title_abstract", seq=1, decision="exclude",
            reason_code="E1", decided_by="ai", decider="agent:screener", confidence=0.9,
        ))
        db.commit()
    item = next(i for i in queue(world["owner"], p)["items"] if i["source_id"] == s)
    assert item["ai_suggestion"] is None


def test_dual_screen_agreement_counts_once_both_reviewers_decide_the_same_way(world):
    p, s = world["pid"], world["ids"][0]
    set_dual_screen(world["owner"], p, True)
    decide(world["owner"], p, s, "include")
    assert decide(world["co_author"], p, s, "include").status_code == 201
    counts = queue(world["owner"], p)["counts"]
    assert counts["include"] == 1 and counts["unscreened"] == 2 and counts["conflict"] == 0
    assert s not in {i["source_id"] for i in queue(world["co_author"], p)["items"]}
    # now that both have decided, the blind is lifted for both
    history = world["co_author"].get(f"/api/projects/{p}/screening/decisions", params={"source_id": s}).json()
    assert {h["decision"] for h in history} == {"include"} and len(history) == 2


def test_dual_screen_disagreement_is_a_conflict_not_a_decision(world):
    p, s = world["pid"], world["ids"][0]
    set_dual_screen(world["owner"], p, True)
    decide(world["owner"], p, s, "include")
    assert decide(world["co_author"], p, s, "exclude", reason_code="E1").status_code == 201
    counts = queue(world["owner"], p)["counts"]
    assert counts["conflict"] == 1 and counts["include"] == 0 and counts["exclude"] == 0 and counts["unscreened"] == 2
    # a conflict is not an agreed decision, so it doesn't pass on to full-text screening
    assert decide(world["owner"], p, s, "include", stage="full_text").status_code == 409


def test_dual_screen_undo_only_takes_back_your_own_decision(world):
    p, s = world["pid"], world["ids"][0]
    set_dual_screen(world["owner"], p, True)
    decide(world["owner"], p, s, "include")
    assert undo(world["co_author"], p, s).status_code == 409  # co-author has nothing of their own to undo
    assert undo(world["owner"], p, s).status_code == 201
    assert s in {i["source_id"] for i in queue(world["owner"], p)["items"]}


def test_dual_screen_requires_agreement_before_full_text(world):
    p, ids = world["pid"], world["ids"]
    set_dual_screen(world["owner"], p, True)
    decide(world["owner"], p, ids[0], "include")
    decide(world["co_author"], p, ids[0], "include")
    assert ids[0] in {i["source_id"] for i in queue(world["owner"], p, "full_text")["items"]}
    decide(world["owner"], p, ids[1], "include")  # only one reviewer so far: not agreed yet
    assert ids[1] not in {i["source_id"] for i in queue(world["owner"], p, "full_text")["items"]}
    assert decide(world["owner"], p, ids[1], "include", stage="full_text").status_code == 409


def agreement(client, pid, stage="title_abstract"):
    return client.get(f"/api/projects/{pid}/screening/agreement", params={"stage": stage}).json()


def test_agreement_with_nothing_decided_reports_zero_comparable_items(world):
    data = agreement(world["owner"], world["pid"])
    assert data["n"] == 0 and data["kappa"] is None
    assert data["scope"] == {"stage_sources": 3, "no_reviewer": 3, "one_reviewer": 0, "two_reviewers": 0, "three_or_more_reviewers": 0}
    assert data["conflicts"] == []


def test_agreement_pairs_two_reviewers_decisions_and_flags_conflicts(world):
    p, ids = world["pid"], world["ids"]
    decide(world["owner"], p, ids[0], "include")
    decide(world["co_author"], p, ids[0], "include")  # agree
    decide(world["owner"], p, ids[1], "exclude", reason_code="E1")
    decide(world["co_author"], p, ids[1], "include")  # disagree
    decide(world["owner"], p, ids[2], "maybe")  # only one reviewer so far
    data = agreement(world["owner"], p)
    assert data["scope"] == {"stage_sources": 3, "no_reviewer": 0, "one_reviewer": 1, "two_reviewers": 2, "three_or_more_reviewers": 0}
    assert data["n"] == 2 and data["agreed"] == 1
    assert len(data["conflicts"]) == 1
    conflict = data["conflicts"][0]
    assert conflict["source_id"] == ids[1]
    assert {d["decision"] for d in conflict["decisions"]} == {"exclude", "include"}


def test_agreement_works_without_dual_screen_mode(world):
    # dual-screen is off by default; a second WRITE-role decision still appends a row even though it
    # becomes "the" decision for queue purposes (latest wins) — agreement still compares both deciders.
    p, s = world["pid"], world["ids"][0]
    decide(world["owner"], p, s, "include")
    decide(world["co_author"], p, s, "exclude", reason_code="E1")
    data = agreement(world["owner"], p)
    assert data["n"] == 1 and data["agreed"] == 0
    assert len(data["conflicts"]) == 1


def test_agreement_bad_stage_is_refused(world):
    assert world["owner"].get(f"/api/projects/{world['pid']}/screening/agreement", params={"stage": "x"}).status_code == 422


def test_agreement_is_readable_by_every_project_role(world):
    p = world["pid"]
    assert world["reviewer"].get(f"/api/projects/{p}/screening/agreement").status_code == 200
    assert world["supervisor"].get(f"/api/projects/{p}/screening/agreement").status_code == 200


def test_aggregate_agreement_figures_are_owner_only(world):
    # owner decision 2026-10-19 (M2.3.2/M2.3.3): the project-wide kappa/counts are owner only, so a
    # co-author/supervisor/reviewer never learns how often reviewers agree, even outside dual-screen.
    p, ids = world["pid"], world["ids"]
    decide(world["owner"], p, ids[0], "include")
    decide(world["co_author"], p, ids[0], "exclude", reason_code="E1")
    owner_view = agreement(world["owner"], p)
    assert owner_view["n"] == 1 and owner_view["kappa"] is not None and owner_view["scope"] is not None
    for who in ("co_author", "supervisor", "reviewer"):
        data = agreement(world[who], p)
        assert data["n"] is None and data["agreed"] is None and data["percent_agreement"] is None
        assert data["kappa"] is None and data["band"] is None and data["categories"] is None
        assert data["matrix"] is None and data["note"] is None and data["scope"] is None, who
        assert data["conflicts"] == owner_view["conflicts"]  # the conflict list itself is unaffected (M2.4.3)


def test_screening_audit_events_are_each_reviewers_own_except_for_the_owner(world):
    # owner decision 2026-10-19 (M2.3.2/M2.3.3): a non-owner's view of the audit trail shows only their
    # own screening-decision events; the owner still sees every reviewer's.
    p, ids = world["pid"], world["ids"]
    decide(world["owner"], p, ids[0], "include")
    decide(world["co_author"], p, ids[1], "exclude", reason_code="E1")

    owner_events = world["owner"].get(f"/api/projects/{p}/audit").json()
    owner_screening = [e for e in owner_events if e["action"].startswith("screening.")]
    assert {e["actor"] for e in owner_screening} == {world["owner_id"], world["co_author"].get("/api/auth/me").json()["id"]}

    co_author_events = world["co_author"].get(f"/api/projects/{p}/audit").json()
    co_author_screening = [e for e in co_author_events if e["action"].startswith("screening.")]
    co_author_id = world["co_author"].get("/api/auth/me").json()["id"]
    assert co_author_screening and all(e["actor"] == co_author_id for e in co_author_screening)

    # non-screening events (e.g. the project/source creation) are unaffected for everyone
    assert "project.created" in {e["action"] for e in co_author_events}

    supervisor_events = world["supervisor"].get(f"/api/projects/{p}/audit").json()
    assert not any(e["action"].startswith("screening.") for e in supervisor_events)  # decided nothing themselves

    # the export endpoint applies the same scoping
    exported = world["co_author"].get(f"/api/projects/{p}/audit/export", params={"format": "json"}).json()
    assert all(e["actor"] == co_author_id for e in exported if e["action"].startswith("screening."))


def test_dual_screen_agreement_hides_a_conflict_from_a_screener_who_has_not_decided_it_but_not_from_one_who_cannot_screen(world):
    # spec 5.3.3 blinding protects people who can still screen; a supervisor cannot, and needs to see a conflict to
    # rule on it (owner decision 2026-10-12, M2.4.3)
    p, s = world["pid"], world["ids"][0]
    set_dual_screen(world["owner"], p, True)
    with SessionLocal() as db:  # two other reviewers disagree; the owner and the co-author have not decided this record
        for seq, (who, decision) in enumerate((("reviewer-a", "include"), ("reviewer-b", "exclude")), start=1):
            db.add(ScreeningDecision(project_id=uuid.UUID(p), source_id=uuid.UUID(s), stage="title_abstract", seq=seq,
                                     decision=decision, reason_code="E1" if decision == "exclude" else None, decided_by="human", decider=who))
        db.commit()
    assert agreement(world["owner"], p)["conflicts"] == []  # can still screen this record: blinded
    assert agreement(world["co_author"], p)["conflicts"] == []
    # the aggregates must not reveal the two decisions on a record the screener has not decided either
    data = agreement(world["owner"], p)  # owner: the only role that sees aggregate figures (2026-10-19 decision)
    assert data["scope"]["two_reviewers"] == 0 and data["n"] == 0 and data["matrix"] == {}
    # co-author, supervisor and reviewer are not the owner: aggregate figures are null for all of them,
    # regardless of whether they themselves are blinded from this conflict
    for who in ("co_author", "supervisor", "reviewer"):
        data = agreement(world[who], p)
        assert data["scope"] is None and data["n"] is None and data["matrix"] is None, who
    assert agreement(world["supervisor"], p)["conflicts"] != []  # cannot screen: not blinded from the conflict list itself
    assert [c["source_id"] for c in agreement(world["supervisor"], p)["conflicts"]] == [s]  # cannot screen: not blinded
    assert [c["source_id"] for c in agreement(world["reviewer"], p)["conflicts"]] == [s]
    # a screener who has decided the record themselves sees it, as they do in the decision history
    assert decide(world["owner"], p, s, "maybe").status_code == 201
    assert [c["source_id"] for c in agreement(world["owner"], p)["conflicts"]] == [s]


def test_auto_exclude_is_off_by_default_and_owner_only_to_change(world):
    p = world["pid"]
    data = world["owner"].get(f"/api/projects/{p}/screening/auto-exclude").json()
    assert data["enabled"] is False and data["confidence_threshold_override"] is None
    assert set_auto_exclude(world["co_author"], p).status_code == 403
    r = set_auto_exclude(world["owner"], p, True, confidence_threshold=0.8)
    assert r.status_code == 200
    assert r.json()["enabled"] is True and r.json()["confidence_threshold"] == 0.8
    actions = {e["action"] for e in world["owner"].get(f"/api/projects/{p}/audit").json()}
    assert "screening.autoexclude_changed" in actions


def test_auto_exclude_counts_a_trusted_high_confidence_exclude_without_a_person(world):
    p, s = world["pid"], world["ids"][0]
    set_auto_exclude(world["owner"], p, True, confidence_threshold=0.8)
    insert_ai_decision(p, s, decision="exclude", confidence=0.95)
    data = queue(world["owner"], p)
    assert data["counts"]["ai_auto_excluded"] == 1 and data["counts"]["unscreened"] == 2
    assert s not in {i["source_id"] for i in data["items"]}
    # a person can still override: their decision always wins
    assert decide(world["owner"], p, s, "include").status_code == 201
    assert queue(world["owner"], p)["counts"]["include"] == 1


def test_after_the_project_disables_prescreen_an_unsampled_ai_exclude_goes_back_to_a_person(world):
    p, s = world["pid"], world["ids"][0]
    set_auto_exclude(world["owner"], p, True, confidence_threshold=0.8)
    insert_ai_decision(p, s, decision="exclude", confidence=0.95)
    assert queue(world["owner"], p)["counts"]["ai_auto_excluded"] == 1
    assert world["owner"].put(f"/api/projects/{p}/screening/ai-prescreen", json={"disabled": True}).status_code == 200
    data = queue(world["owner"], p)
    assert data["counts"]["ai_auto_excluded"] == 0 and data["counts"]["unscreened"] == 3
    assert s in {i["source_id"] for i in data["items"]}


def test_auto_exclude_leaves_a_low_confidence_exclude_for_a_person(world):
    p, s = world["pid"], world["ids"][0]
    set_auto_exclude(world["owner"], p, True, confidence_threshold=0.9)
    insert_ai_decision(p, s, decision="exclude", confidence=0.5)
    data = queue(world["owner"], p)
    assert data["counts"]["ai_auto_excluded"] == 0 and data["counts"]["unscreened"] == 3
    item = next(i for i in data["items"] if i["source_id"] == s)
    assert item["ai_suggestion"]["confidence"] == 0.5


def test_auto_exclude_is_off_unless_the_project_turns_it_on(world):
    p, s = world["pid"], world["ids"][0]
    insert_ai_decision(p, s, decision="exclude", confidence=0.99)  # would be eligible, but the project never enabled it
    data = queue(world["owner"], p)
    assert "ai_auto_excluded" not in data["counts"] and data["counts"]["unscreened"] == 3


def test_a_pending_sampled_exclude_stays_in_the_queue_until_reviewed(world):
    p, s = world["pid"], world["ids"][0]
    set_auto_exclude(world["owner"], p, True, confidence_threshold=0.8)
    decision_id = insert_ai_decision(p, s, decision="exclude", confidence=0.95)
    with SessionLocal() as db:
        db.add(AutoExcludeAudit(project_id=uuid.UUID(p), source_id=uuid.UUID(s), stage="title_abstract", decision_id=decision_id, confidence=0.95))
        db.commit()
    data = queue(world["owner"], p)
    assert data["counts"]["ai_auto_excluded"] == 0 and data["counts"]["unscreened"] == 3
    assert s in {i["source_id"] for i in data["items"]}
    audits = world["owner"].get(f"/api/projects/{p}/screening/auto-exclude/audits").json()
    assert len(audits) == 1 and audits[0]["status"] == "pending" and audits[0]["source_id"] == s


def test_confirming_a_sampled_exclude_makes_it_count(world):
    p, s = world["pid"], world["ids"][0]
    set_auto_exclude(world["owner"], p, True, confidence_threshold=0.8)
    decision_id = insert_ai_decision(p, s, decision="exclude", confidence=0.95)
    with SessionLocal() as db:
        audit_row = AutoExcludeAudit(project_id=uuid.UUID(p), source_id=uuid.UUID(s), stage="title_abstract", decision_id=decision_id, confidence=0.95)
        db.add(audit_row)
        db.commit()
        db.refresh(audit_row)
        audit_id = str(audit_row.id)
    assert world["co_author"].post(f"/api/projects/{p}/screening/auto-exclude/audits/{audit_id}/review", json={"outcome": "confirm"}).status_code == 200
    data = queue(world["owner"], p)
    assert data["counts"]["ai_auto_excluded"] == 1
    # reviewing it again is refused
    again = world["owner"].post(f"/api/projects/{p}/screening/auto-exclude/audits/{audit_id}/review", json={"outcome": "overturn"})
    assert again.status_code == 409



def test_a_review_that_loses_the_race_is_refused_and_writes_no_audit_event(world):
    from datetime import datetime, timezone

    from sqlalchemy import event, update

    from app.database import engine
    from app.models import AuditEvent

    p, s = world["pid"], world["ids"][0]
    set_auto_exclude(world["owner"], p, True, confidence_threshold=0.8)
    decision_id = insert_ai_decision(p, s, decision="exclude", confidence=0.95)
    with SessionLocal() as db:
        audit_row = AutoExcludeAudit(project_id=uuid.UUID(p), source_id=uuid.UUID(s), stage="title_abstract", decision_id=decision_id, confidence=0.95)
        db.add(audit_row)
        db.commit()
        db.refresh(audit_row)
        audit_id = str(audit_row.id)
    # The endpoint's pre-check reads 'pending'; a rival review commits between that read and the
    # conditional UPDATE. Injected on the same connection, so the rival's change is inside the request
    # transaction and the conditional UPDATE sees rowcount 0 (the 409 detail differs from the pre-check).
    fired = []

    def rival_review(conn, cursor, statement, parameters, context, executemany):
        if fired or not statement.lstrip().upper().startswith("UPDATE AUTO_EXCLUDE_AUDITS"):
            return
        fired.append(statement)
        conn.execute(
            update(AutoExcludeAudit.__table__).values(status="confirmed", reviewed_by="rival", reviewed_at=datetime.now(timezone.utc))
        )

    event.listen(engine, "before_cursor_execute", rival_review)
    try:
        resp = world["owner"].post(f"/api/projects/{p}/screening/auto-exclude/audits/{audit_id}/review", json={"outcome": "overturn"})
    finally:
        event.remove(engine, "before_cursor_execute", rival_review)
    assert fired, "the rival review was never injected"
    assert resp.status_code == 409 and resp.json()["detail"] == "This item was already reviewed."
    with SessionLocal() as db:
        assert db.query(AuditEvent).filter(AuditEvent.action == "screening.autoexclude_reviewed").count() == 0


def test_the_orm_refuses_a_second_review_even_when_the_endpoint_check_is_bypassed(world):
    from datetime import datetime, timezone

    p, s = world["pid"], world["ids"][0]
    set_auto_exclude(world["owner"], p, True, confidence_threshold=0.8)
    decision_id = insert_ai_decision(p, s, decision="exclude", confidence=0.95)
    with SessionLocal() as db:
        audit_row = AutoExcludeAudit(project_id=uuid.UUID(p), source_id=uuid.UUID(s), stage="title_abstract", decision_id=decision_id, confidence=0.95)
        db.add(audit_row)
        db.commit()
        db.refresh(audit_row)
        audit_id = audit_row.id
    with SessionLocal() as db:
        row = db.get(AutoExcludeAudit, audit_id)
        row.status, row.reviewed_by, row.reviewed_at = "confirmed", "first", datetime.now(timezone.utc)
        db.commit()
    with SessionLocal() as db:
        row = db.get(AutoExcludeAudit, audit_id)
        row.status, row.reviewed_by, row.reviewed_at = "overturned", "second", datetime.now(timezone.utc)
        # the DB CHECK alone would accept this (both review fields set); only the ORM guard refuses it
        with pytest.raises(ValueError, match="cannot be reviewed again"):
            db.commit()
        db.rollback()
    with SessionLocal() as db:
        assert db.get(AutoExcludeAudit, audit_id).status == "confirmed"


def test_overturning_a_sampled_exclude_returns_it_to_the_queue(world):
    p, s = world["pid"], world["ids"][0]
    set_auto_exclude(world["owner"], p, True, confidence_threshold=0.8)
    decision_id = insert_ai_decision(p, s, decision="exclude", confidence=0.95)
    with SessionLocal() as db:
        audit_row = AutoExcludeAudit(project_id=uuid.UUID(p), source_id=uuid.UUID(s), stage="title_abstract", decision_id=decision_id, confidence=0.95)
        db.add(audit_row)
        db.commit()
        db.refresh(audit_row)
        audit_id = str(audit_row.id)
    r = world["owner"].post(f"/api/projects/{p}/screening/auto-exclude/audits/{audit_id}/review", json={"outcome": "overturn"})
    assert r.status_code == 200 and r.json()["status"] == "overturned"
    data = queue(world["owner"], p)
    assert data["counts"]["ai_auto_excluded"] == 0 and s in {i["source_id"] for i in data["items"]}
    actions = {e["action"] for e in world["owner"].get(f"/api/projects/{p}/audit").json()}
    assert "screening.autoexclude_reviewed" in actions


def test_auto_exclude_audit_review_is_write_role_only(world):
    p, s = world["pid"], world["ids"][0]
    set_auto_exclude(world["owner"], p, True, confidence_threshold=0.8)
    decision_id = insert_ai_decision(p, s, decision="exclude", confidence=0.95)
    with SessionLocal() as db:
        audit_row = AutoExcludeAudit(project_id=uuid.UUID(p), source_id=uuid.UUID(s), stage="title_abstract", decision_id=decision_id, confidence=0.95)
        db.add(audit_row)
        db.commit()
        db.refresh(audit_row)
        audit_id = str(audit_row.id)
    r = world["reviewer"].post(f"/api/projects/{p}/screening/auto-exclude/audits/{audit_id}/review", json={"outcome": "confirm"})
    assert r.status_code == 403


def test_auto_exclude_does_not_apply_under_dual_screen(world):
    p, s = world["pid"], world["ids"][0]
    set_dual_screen(world["owner"], p, True)
    set_auto_exclude(world["owner"], p, True, confidence_threshold=0.8)
    insert_ai_decision(p, s, decision="exclude", confidence=0.99)
    data = queue(world["owner"], p)
    assert data["counts"]["ai_auto_excluded"] == 0
    assert s in {i["source_id"] for i in data["items"]}


def test_ai_agreement_endpoint_reports_zero_with_nothing_comparable(world):
    p = world["pid"]
    data = world["owner"].get(f"/api/projects/{p}/screening/ai-agreement").json()
    assert data["n"] == 0 and data["kappa"] is None and data["prescreen_disabled"] is False


def test_ai_agreement_endpoint_pairs_the_ai_suggestion_with_the_persons_own_decision(world):
    p, s = world["pid"], world["ids"][0]
    insert_ai_decision(p, s, decision="exclude", confidence=0.95, reason_code="E1")
    decide(world["owner"], p, s, "include")
    data = world["owner"].get(f"/api/projects/{p}/screening/ai-agreement").json()
    assert data["n"] == 1 and data["agreed"] == 0


def test_ai_prescreen_status_is_owner_only_to_change_and_readable_by_all(world):
    p = world["pid"]
    assert world["owner"].get(f"/api/projects/{p}/screening/ai-agreement").json()["prescreen_disabled"] is False
    assert world["co_author"].put(f"/api/projects/{p}/screening/ai-prescreen", json={"disabled": True}).status_code == 403
    r = world["owner"].put(f"/api/projects/{p}/screening/ai-prescreen", json={"disabled": True})
    assert r.status_code == 200 and r.json()["disabled"] is True and r.json()["reason"]
    assert world["owner"].post(f"/api/projects/{p}/screening/prescreen").status_code == 409
    r = world["owner"].put(f"/api/projects/{p}/screening/ai-prescreen", json={"disabled": False})
    assert r.status_code == 200 and r.json()["disabled"] is False and r.json()["reason"] is None
    actions = [e["action"] for e in world["owner"].get(f"/api/projects/{p}/audit").json()]
    assert actions.count("screening.ai_prescreen_disabled_changed") == 2


def set_active_learning(client, pid, enabled=True):
    return client.put(f"/api/projects/{pid}/screening/active-learning", json={"enabled": enabled})


def test_active_learning_is_off_by_default_and_owner_only_to_change(world):
    p = world["pid"]
    assert world["owner"].get(f"/api/projects/{p}/screening/active-learning").json() == {"enabled": False}
    assert set_active_learning(world["co_author"], p).status_code == 403
    r = set_active_learning(world["owner"], p, True)
    assert r.status_code == 200 and r.json() == {"enabled": True}
    actions = {e["action"] for e in world["owner"].get(f"/api/projects/{p}/audit").json()}
    assert "screening.active_learning_changed" in actions


def test_relevance_sort_falls_back_to_oldest_without_both_decision_classes(world):
    p = world["pid"]
    set_active_learning(world["owner"], p, True)
    oldest = world["owner"].get(f"/api/projects/{p}/screening/queue").json()
    data = world["owner"].get(f"/api/projects/{p}/screening/queue", params={"sort": "relevance"}).json()
    assert data["sort"] == "oldest"
    assert [i["source_id"] for i in data["items"]] == [i["source_id"] for i in oldest["items"]]


def test_relevance_sort_is_ignored_unless_the_project_enables_it(world):
    p = world["pid"]
    decide(world["owner"], p, world["ids"][0], "include")
    decide(world["owner"], p, world["ids"][1], "exclude", reason_code="E1")
    data = world["owner"].get(f"/api/projects/{p}/screening/queue", params={"sort": "relevance"}).json()
    assert data["sort"] == "oldest"  # active_learning_enabled was never turned on


def test_bad_sort_value_is_refused(world):
    p = world["pid"]
    assert world["owner"].get(f"/api/projects/{p}/screening/queue", params={"sort": "bogus"}).status_code == 422


def test_relevance_sort_ranks_the_closest_unscreened_record_first(world):
    p = world["pid"]
    set_active_learning(world["owner"], p, True)
    decide(world["owner"], p, world["ids"][0], "include")
    decide(world["owner"], p, world["ids"][1], "exclude", reason_code="E1")
    with SessionLocal() as db:
        db.get(Source, uuid.UUID(world["ids"][0])).abstract = "A randomised controlled trial of remote work and wellbeing outcomes."
        db.get(Source, uuid.UUID(world["ids"][1])).abstract = "An opinion editorial commentary piece, not empirical."
        db.commit()
    close = world["owner"].post(
        f"/api/projects/{p}/sources", json={"title": "Close match", "abstract": "An empirical randomised trial of remote work wellbeing."}
    ).json()["id"]
    far = world["owner"].post(
        f"/api/projects/{p}/sources", json={"title": "Far match", "abstract": "An editorial opinion commentary on policy."}
    ).json()["id"]
    data = world["owner"].get(f"/api/projects/{p}/screening/queue", params={"sort": "relevance"}).json()
    assert data["sort"] == "relevance"
    order = [i["source_id"] for i in data["items"]]
    assert order.index(close) < order.index(far)


def test_under_dual_screen_another_reviewers_decisions_do_not_reorder_your_relevance_queue(world):
    # M2.6 blinding leak: a record the co-author decided stays in the owner's queue (dual-screen), so
    # training the ranking on the co-author's answer would reveal it by rank: their include rising to
    # the top, their exclude sinking to the bottom. The owner's ranking must not move.
    p, owner, co_author = world["pid"], world["owner"], world["co_author"]
    set_dual_screen(owner, p, True)
    set_active_learning(owner, p, True)
    texts = [
        ("Glacier meltwater hydrology", "Alpine glacier meltwater discharge measured across seasons."),
        ("Medieval tax ledgers", "Archival ledgers of medieval tithe collection in monasteries."),
        ("Coral reef bleaching", "Ocean warming drives coral bleaching on tropical reefs."),
        ("Railway timetable optimisation", "Scheduling commuter trains with integer programming."),
    ]
    added = [owner.post(f"/api/projects/{p}/sources", json={"title": t, "abstract": a}).json()["id"] for t, a in texts]
    params = {"sort": "relevance"}
    before = owner.get(f"/api/projects/{p}/screening/queue", params=params).json()
    assert decide(co_author, p, added[3], "include").status_code == 201
    assert decide(co_author, p, added[2], "exclude", reason_code="E1").status_code == 201
    after = owner.get(f"/api/projects/{p}/screening/queue", params=params).json()
    order = [i["source_id"] for i in after["items"]]
    assert added[3] in order and added[2] in order  # still the owner's to decide
    assert order == [i["source_id"] for i in before["items"]]
    assert after["sort"] == before["sort"] == "oldest"  # nothing the owner may learn from yet


def test_oldest_sort_is_still_available_and_default(world):
    p = world["pid"]
    set_active_learning(world["owner"], p, True)
    decide(world["owner"], p, world["ids"][0], "include")
    data = world["owner"].get(f"/api/projects/{p}/screening/queue").json()
    assert data["sort"] == "oldest"


# ---- M2.4.3: an owner or supervisor rules on a reviewer conflict ---------------------------------

def _conflict(world, owner_says="include", co_author_says="maybe"):
    """Dual-screen on; the owner and the co-author disagree about Paper 0. Returns the project id and source id."""
    p, s = world["pid"], world["ids"][0]
    assert set_dual_screen(world["owner"], p).status_code == 200
    assert decide(world["owner"], p, s, owner_says).status_code == 201
    assert decide(world["co_author"], p, s, co_author_says).status_code == 201
    return p, s


def adjudicate(client, pid, source_id, decision="exclude", **extra):
    return client.post(f"/api/projects/{pid}/screening/decisions/adjudicate", json={"source_id": source_id, "decision": decision, **extra})


def _title_abstract_flow(world):
    return world["owner"].get(f"/api/projects/{world['pid']}/prisma").json()["screening"]["title_abstract"]


def test_a_supervisors_ruling_settles_a_conflict_and_counts_with_its_reason(world):
    p, s = _conflict(world)
    assert _title_abstract_flow(world)["conflicts"] == 1 and _title_abstract_flow(world)["excluded"] == 0
    ruling = adjudicate(world["supervisor"], p, s, "exclude", reason_code="E1")
    assert ruling.status_code == 201
    assert ruling.json()["decided_by"] == "arbiter"
    flow = _title_abstract_flow(world)
    assert flow["conflicts"] == 0 and flow["excluded"] == 1 and flow["excluded_by_reason"] == {"E1": 1}
    assert world["owner"].get(f"/api/projects/{p}/prisma").json()["screening"]["exclusion_reasons_missing"] == 0


def test_a_ruling_leaves_both_reviewers_rows_and_the_kappa_pair_alone(world):
    p, s = _conflict(world)
    adjudicate(world["supervisor"], p, s, "include")
    history = world["owner"].get(f"/api/projects/{p}/screening/decisions", params={"source_id": s}).json()
    assert sorted(h["decided_by"] for h in history) == ["arbiter", "human", "human"]
    agreement = world["owner"].get(f"/api/projects/{p}/screening/agreement").json()
    assert agreement["n"] == 1 and agreement["agreed"] == 0  # the two reviewers' own answers still disagree
    (conflict,) = agreement["conflicts"]
    assert conflict["adjudication"]["decision"] == "include"


def test_only_an_owner_or_supervisor_may_rule(world):
    p, s = _conflict(world)
    assert adjudicate(world["reviewer"], p, s, "include").status_code == 403
    assert adjudicate(world["co_author"], p, s, "include").status_code == 403  # a writer, but not an owner or supervisor
    assert adjudicate(world["supervisor"], p, s, "include").status_code == 201


def test_someone_who_decided_the_source_cannot_rule_on_it(world):
    p, s = _conflict(world)
    refused = adjudicate(world["owner"], p, s, "include")
    assert refused.status_code == 409 and "decided this source yourself" in refused.json()["detail"]
    assert _title_abstract_flow(world)["conflicts"] == 1


def test_only_a_real_conflict_can_be_ruled_on_and_only_under_dual_screen(world):
    p, s = world["pid"], world["ids"][0]
    assert adjudicate(world["supervisor"], p, s, "include").status_code == 409  # dual-screen is off
    set_dual_screen(world["owner"], p)
    assert adjudicate(world["supervisor"], p, s, "include").status_code == 409  # nobody has decided
    decide(world["owner"], p, s, "include")
    decide(world["co_author"], p, s, "include")
    assert adjudicate(world["supervisor"], p, s, "exclude", reason_code="E1").status_code == 409  # they agree


def test_an_exclusion_ruling_needs_one_of_the_projects_exclusion_codes(world):
    p, s = _conflict(world)
    assert adjudicate(world["supervisor"], p, s, "exclude").status_code == 422
    assert adjudicate(world["supervisor"], p, s, "exclude", reason_code="I1").status_code == 422
    assert adjudicate(world["supervisor"], p, s, "exclude", reason_code="E2").status_code == 201


def test_a_reviewer_who_changes_their_answer_after_a_ruling_reopens_the_conflict(world):
    p, s = _conflict(world)
    adjudicate(world["supervisor"], p, s, "exclude", reason_code="E1")
    assert undo(world["owner"], p, s).status_code == 201  # the owner takes their answer back
    assert decide(world["owner"], p, s, "exclude", reason_code="E1").status_code == 201  # and gives a third, different one
    flow = _title_abstract_flow(world)
    assert flow["conflicts"] == 1 and flow["excluded"] == 0  # the earlier ruling no longer stands


def test_a_later_ruling_replaces_an_earlier_one(world):
    p, s = _conflict(world)
    adjudicate(world["supervisor"], p, s, "exclude", reason_code="E1")
    assert adjudicate(world["supervisor"], p, s, "include").status_code == 201
    flow = _title_abstract_flow(world)
    assert flow["included"] == 1 and flow["excluded"] == 0


def test_a_locked_screening_cannot_be_ruled_on(world):
    p, s = _conflict(world)
    with SessionLocal() as db:
        gate = db.scalar(select(Gate).where(Gate.project_id == uuid.UUID(p), Gate.code == GateCode.G3))
        if gate is None:
            gate = Gate(project_id=uuid.UUID(p), code=GateCode.G3)
            db.add(gate)
        gate.status = GateStatus.approved
        db.commit()
    assert adjudicate(world["supervisor"], p, s, "include").status_code == 409


def test_queue_pages_through_more_than_one_page_and_reports_every_pending_record(world):
    # M2.3.4: the screen used to load only the first 200 records and then say "Nothing left to screen".
    owner, pid = world["owner"], world["pid"]
    more = [owner.post(f"/api/projects/{pid}/sources", json={"title": f"Extra {n}"}).json()["id"] for n in range(202)]
    everything = set(world["ids"]) | set(more)
    first = queue(owner, pid)
    assert first["pending"] == len(everything) == 205
    assert len(first["items"]) == 50  # the default page size is unchanged
    seen = []
    offset = 0
    while True:
        page = owner.get(f"/api/projects/{pid}/screening/queue", params={"stage": "title_abstract", "limit": 200, "offset": offset}).json()
        assert page["pending"] == 205
        if not page["items"]:
            break
        seen += [item["source_id"] for item in page["items"]]
        offset += 200
    assert len(seen) == len(set(seen)) == 205
    assert set(seen) == everything
    decide(owner, pid, world["ids"][0], "include")
    assert queue(owner, pid)["pending"] == 204
