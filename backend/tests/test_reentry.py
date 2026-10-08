"""Plan M0.5.5 / spec 13.5: re-entering an earlier stage marks downstream artifacts stale with a clear re-run path."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import artifacts as art
from app import task_queue as q
from app import task_registry as registry
from gate_helpers import approve_earlier_gates
from app.database import SessionLocal
from app.main import app
from app.models import (
    Artifact, ArtifactStatus, AuditEvent, Gate, GateCode, GateStatus, Project, ProjectMember, ProjectRole,
    ProjectStage as S, Source, TaskStatus,
)


def _login(email):
    client = TestClient(app)
    user_id = client.post("/api/auth/development/login", json={"email": email, "display_name": "R"}).json()["id"]
    return client, user_id


@pytest.fixture
def world():
    tag = uuid.uuid4().hex[:8]
    owner, owner_id = _login(f"re-owner-{tag}@example.com")
    project_id = owner.post("/api/projects", json={"title": "Re-entry"}).json()["id"]
    clients = {"owner": owner}
    for role in (ProjectRole.supervisor, ProjectRole.co_author, ProjectRole.reviewer):
        client, uid = _login(f"re-{role.value}-{tag}@example.com")
        with SessionLocal() as db:
            db.add(ProjectMember(project_id=uuid.UUID(project_id), user_id=uuid.UUID(uid), role=role))
            db.commit()
        clients[role.value] = client
    return {"clients": clients, "project": project_id, "owner_id": owner_id, "pid": uuid.UUID(project_id)}


def _set_stage(world, stage):
    with SessionLocal() as db:
        db.get(Project, world["pid"]).stage = stage
        db.commit()


def _artifact(world, kind, stage, ref="", depends_on=()):
    with SessionLocal() as db:
        ups = tuple(db.get(Artifact, d) for d in depends_on)
        artifact = art.register_artifact(db, world["pid"], kind, ref, stage, actor="tester", depends_on=ups)
        db.commit()
        return artifact.id


def _get(model, key):
    with SessionLocal() as db:
        row = db.get(model, key)
        db.expunge(row)
        return row


def _reenter(world, target, reason="Rework needed", who="owner"):
    return world["clients"][who].post(f"/api/projects/{world['project']}/stage/reenter", json={"stage": target, "reason": reason})


def _approve(world, code):
    approve_earlier_gates(world["project"], code)  # gates go in order (M0.5.10)
    return world["clients"]["owner"].post(f"/api/projects/{world['project']}/gates/{code}/approve", json={"note": "ok"})


def _gates(world):
    with SessionLocal() as db:
        return {g.code: g for g in db.scalars(select(Gate).where(Gate.project_id == world["pid"]))}


# ---- artifacts and edges ---------------------------------------------------------------------

def test_registering_is_idempotent_per_project_kind_and_ref(world):
    first = _artifact(world, "search", S.retrieved, "q1")
    again = _artifact(world, "search", S.retrieved, "q1")
    other = _artifact(world, "search", S.retrieved, "q2")

    assert first == again and first != other
    assert _get(Artifact, first).status == ArtifactStatus.current


def test_edges_refuse_self_cross_project_and_cyclic_dependencies(world):
    a = _artifact(world, "a", S.retrieved)
    b = _artifact(world, "b", S.screened, depends_on=[a])
    c = _artifact(world, "c", S.extracted, depends_on=[b])
    other_owner, _ = _login(f"re-other-{uuid.uuid4().hex[:6]}@example.com")
    other_pid = uuid.UUID(other_owner.post("/api/projects", json={"title": "Other"}).json()["id"])

    with SessionLocal() as db:
        A, B, C = (db.get(Artifact, x) for x in (a, b, c))
        with pytest.raises(art.ArtifactError, match="itself"):
            art.add_edge(db, A, A)
        with pytest.raises(art.ArtifactError, match="cycle"):
            art.add_edge(db, C, A)  # a -> b -> c, so c -> a would close a loop
        foreign = art.register_artifact(db, other_pid, "x", "", S.retrieved, actor="t")
        with pytest.raises(art.ArtifactError, match="same project"):
            art.add_edge(db, A, foreign)
        art.add_edge(db, A, B)  # repeating an existing edge is harmless
        db.commit()
        assert len(db.scalars(select(art.ArtifactEdge).where(art.ArtifactEdge.project_id == world["pid"])).all()) == 2


def test_a_new_artifact_built_from_a_stale_one_starts_stale(world):
    _set_stage(world, S.screened)
    old = _artifact(world, "old", S.screened)
    assert _reenter(world, "scoped").status_code == 200

    new = _artifact(world, "derived", S.scoped, depends_on=[old])

    derived = _get(Artifact, new)
    assert derived.status == ArtifactStatus.stale and "Depends on stale artifact old" in derived.stale_reason


# ---- re-entry: artifacts ---------------------------------------------------------------------

def test_re_entry_marks_only_later_stage_artifacts_stale_and_records_why(world):
    _set_stage(world, S.screened)
    early = _artifact(world, "scoping", S.scoped)
    at_target = _artifact(world, "search_plan", S.search_planned)
    later = _artifact(world, "screening", S.screened)

    response = _reenter(world, "search_planned", "The search terms were too narrow")

    assert response.status_code == 200
    body = response.json()
    assert [a["kind"] for a in body["stale_artifacts"]] == ["screening"]
    assert _get(Artifact, early).status == ArtifactStatus.current
    assert _get(Artifact, at_target).status == ArtifactStatus.current  # the stage we went back *to* stays valid
    stale = _get(Artifact, later)
    assert stale.status == ArtifactStatus.stale and stale.stale_at is not None
    assert "Re-entered 'search_planned' from 'screened': The search terms were too narrow" == stale.stale_reason


def test_staleness_follows_dependency_edges_even_into_an_earlier_stage(world):
    _set_stage(world, S.extracted)
    later = _artifact(world, "extraction", S.extracted)
    brief = _artifact(world, "revised_brief", S.scoped, depends_on=[later])  # an early artifact built from a later one
    unrelated = _artifact(world, "scoping_note", S.scoped)

    _reenter(world, "search_planned")

    assert _get(Artifact, later).status == ArtifactStatus.stale
    assert _get(Artifact, brief).status == ArtifactStatus.stale
    assert "Depends on an artifact made stale" in _get(Artifact, brief).stale_reason
    assert _get(Artifact, unrelated).status == ArtifactStatus.current


def test_a_transitive_chain_all_goes_stale(world):
    _set_stage(world, S.analyzed)
    a = _artifact(world, "a", S.retrieved)  # earlier than the target below
    b = _artifact(world, "b", S.extracted, depends_on=[a])
    c = _artifact(world, "c", S.synthesized, depends_on=[b])
    d = _artifact(world, "d", S.scoped, depends_on=[c])  # earlier stage, but downstream of c

    _reenter(world, "retrieved")

    assert [_get(Artifact, x).status.value for x in (a, b, c, d)] == ["current", "stale", "stale", "stale"]


def test_re_entry_deletes_nothing(world):
    _set_stage(world, S.screened)
    with SessionLocal() as db:
        db.add(Source(project_id=world["pid"], title="Kept"))
        db.commit()
    keep = _artifact(world, "screening", S.screened)

    _reenter(world, "idea")

    with SessionLocal() as db:
        assert db.scalars(select(Source).where(Source.project_id == world["pid"])).one().title == "Kept"
    assert _get(Artifact, keep).status == ArtifactStatus.stale  # still there, flagged


def test_a_regenerated_artifact_can_be_refreshed_without_touching_what_depends_on_it(world):
    _set_stage(world, S.extracted)
    upstream = _artifact(world, "upstream", S.screened)
    downstream = _artifact(world, "downstream", S.extracted, depends_on=[upstream])
    _reenter(world, "search_planned")

    with SessionLocal() as db:
        art.refresh_artifact(db, db.get(Artifact, upstream))
        db.commit()

    assert _get(Artifact, upstream).status == ArtifactStatus.current and _get(Artifact, upstream).stale_reason is None
    assert _get(Artifact, downstream).status == ArtifactStatus.stale  # nothing is auto-refreshed


# ---- re-entry: stage, gates, audit -----------------------------------------------------------

def test_the_project_moves_back_and_gates_for_later_stages_are_voided(world):
    for code in ("G1", "G2", "G3", "G4"):
        assert _approve(world, code).status_code == 200
    _set_stage(world, S.extracted)

    body = _reenter(world, "search_planned", "Redo the search").json()

    assert body["from_stage"] == "extracted" and body["to_stage"] == "search_planned"
    assert body["gates_reset"] == ["G3", "G4"]
    gates = _gates(world)
    assert gates[GateCode.G1].status == gates[GateCode.G2].status == GateStatus.approved  # stages up to the target stand
    for code in (GateCode.G3, GateCode.G4):
        g = gates[code]
        assert g.status == GateStatus.pending and g.decided_by is None and g.decided_at is None and g.note is None
    assert world["clients"]["owner"].get(f"/api/projects/{world['project']}").json()["stage"] == "search_planned"


def test_going_back_to_the_very_start_voids_every_approval(world):
    _approve(world, "G1")
    _set_stage(world, S.scoped)

    body = _reenter(world, "idea").json()

    assert body["gates_reset"] == ["G1"]
    assert {g.status for g in _gates(world).values()} == {GateStatus.pending}


def test_gates_never_decided_are_left_alone_and_unreached_stages_cost_nothing(world):
    _set_stage(world, S.screened)  # but no gate was ever approved

    body = _reenter(world, "scoped").json()

    assert body["gates_reset"] == []
    with SessionLocal() as db:
        events = [e.action for e in db.scalars(select(AuditEvent).where(AuditEvent.project_id == world["pid"]))]
    assert "gate.reset" not in events and "project.reentered" in events


def test_re_entry_is_audited_with_who_why_and_the_decisions_it_voided(world):
    _approve(world, "G1")
    _approve(world, "G2")
    _set_stage(world, S.search_planned)
    _artifact(world, "plan", S.search_planned)

    _reenter(world, "scoped", "Scope changed after supervisor feedback")

    with SessionLocal() as db:
        events = {e.action: e for e in db.scalars(select(AuditEvent).where(AuditEvent.project_id == world["pid"]))}
    entered = events["project.reentered"]
    assert entered.actor == world["owner_id"]
    assert entered.payload_json == {"from": "search_planned", "to": "scoped", "reason": "Scope changed after supervisor feedback",
                                    "stale": 1, "gates_reset": ["G2"]}
    reset = events["gate.reset"]
    assert reset.payload_json["gate"] == "G2" and reset.payload_json["previous_status"] == "approved"
    assert reset.payload_json["previous_decided_by"] == world["owner_id"]  # the history survives the reset


def test_a_voided_gate_blocks_new_work_until_a_person_approves_it_again(world):
    registry.register("screening_job", requires_gate=GateCode.G3)(lambda task: None)
    try:
        _approve(world, "G3")
        _set_stage(world, S.screened)
        with SessionLocal() as db:
            assert q.enqueue_task(db, world["pid"], "screening_job").status == TaskStatus.queued
            db.commit()

        _reenter(world, "scoped")

        with SessionLocal() as db:
            blocked = q.enqueue_task(db, world["pid"], "screening_job")
            db.commit()
            assert blocked.status == TaskStatus.blocked and blocked.blocked_by_gate == GateCode.G3
        assert _approve(world, "G3").status_code == 200  # a fresh human decision releases it
        with SessionLocal() as db:
            assert db.get(type(blocked), blocked.id).status == TaskStatus.queued
    finally:
        registry.HANDLERS.pop("screening_job", None)
        registry.REQUIRED_GATES.pop("screening_job", None)


# ---- the API ---------------------------------------------------------------------------------

def test_the_response_spells_out_the_stages_to_redo_with_their_gates(world):
    _set_stage(world, S.screened)

    body = _reenter(world, "scoped").json()

    assert body["stages_to_redo"] == [
        {"stage": "search_planned", "gate": "G2"}, {"stage": "retrieved", "gate": None}, {"stage": "screened", "gate": "G3"},
    ]


@pytest.mark.parametrize("target", ["screened", "extracted", "accepted"])
def test_re_entry_must_go_to_an_earlier_stage(world, target):
    _set_stage(world, S.screened)  # target 'screened' is the same stage; the others are later

    response = _reenter(world, target)

    assert response.status_code == 409 and "earlier stage" in response.json()["detail"]
    assert world["clients"]["owner"].get(f"/api/projects/{world['project']}").json()["stage"] == "screened"  # nothing changed


def test_invalid_requests_are_rejected_and_change_nothing(world):
    _set_stage(world, S.screened)
    keep = _artifact(world, "screening", S.screened)

    assert _reenter(world, "no_such_stage").status_code == 422
    assert _reenter(world, "scoped", reason="   ").status_code == 422
    assert world["clients"]["owner"].post(f"/api/projects/{world['project']}/stage/reenter", json={"stage": "scoped"}).status_code == 422

    assert _get(Artifact, keep).status == ArtifactStatus.current


@pytest.mark.parametrize("who", ["supervisor", "co_author", "reviewer"])
def test_only_an_owner_can_re_enter(world, who):
    _set_stage(world, S.screened)
    keep = _artifact(world, "screening", S.screened)

    assert _reenter(world, "scoped", who=who).status_code == 403

    assert _get(Artifact, keep).status == ArtifactStatus.current
    assert world["clients"]["owner"].get(f"/api/projects/{world['project']}").json()["stage"] == "screened"


def test_artifacts_can_be_listed_and_filtered_by_status(world):
    _set_stage(world, S.screened)
    _artifact(world, "keep", S.scoped)
    _artifact(world, "redo", S.screened)
    owner = world["clients"]["owner"]
    base = f"/api/projects/{world['project']}/artifacts"

    _reenter(world, "scoped")

    assert {a["kind"] for a in owner.get(base).json()} == {"keep", "redo"}
    stale = owner.get(base, params={"status": "stale"}).json()
    assert [a["kind"] for a in stale] == ["redo"] and stale[0]["status"] == "stale" and stale[0]["stage"] == "screened"
    assert [a["kind"] for a in owner.get(base, params={"status": "current"}).json()] == ["keep"]
    assert owner.get(base, params={"status": "bogus"}).status_code == 422
    assert world["clients"]["reviewer"].get(base).status_code == 200  # everyone on the project can see what is stale


def test_the_rerun_path_groups_stale_work_by_stage_in_order_with_each_gates_status(world):
    _approve(world, "G3")
    _set_stage(world, S.extracted)
    _artifact(world, "extraction", S.extracted, "e1")
    _artifact(world, "screening", S.screened, "s1")
    _artifact(world, "screening_audit", S.screened, "s2")
    _artifact(world, "retrieval", S.retrieved)
    owner = world["clients"]["owner"]
    _reenter(world, "search_planned")

    path = owner.get(f"/api/projects/{world['project']}/rerun-path").json()

    assert [(s["stage"], s["gate"], s["gate_status"], [a["kind"] for a in s["artifacts"]]) for s in path] == [
        ("retrieved", None, None, ["retrieval"]),
        ("screened", "G3", "pending", ["screening", "screening_audit"]),
        ("extracted", "G4", "pending", ["extraction"]),
    ]
    _approve(world, "G3")  # a person re-approves once the work is redone
    after = owner.get(f"/api/projects/{world['project']}/rerun-path").json()
    assert next(s for s in after if s["stage"] == "screened")["gate_status"] == "approved"


def test_the_rerun_path_is_empty_when_nothing_is_stale(world):
    assert world["clients"]["owner"].get(f"/api/projects/{world['project']}/rerun-path").json() == []


# ---- research runs are artifacts ---------------------------------------------------------------

def test_a_completed_research_run_becomes_an_artifact_and_goes_stale_on_re_entry(world, fake_llm):
    owner = world["clients"]["owner"]
    source_id = owner.post(f"/api/projects/{world['project']}/sources", json={"title": "S", "evidence_excerpt": "Participation rose."}).json()["id"]
    owner.post(f"/api/projects/{world['project']}/sources/{source_id}/verify")  # cited sources must be verified (M1.10.3)
    run = owner.post(f"/api/projects/{world['project']}/research-runs", json={"question": "A long enough question?"}).json()
    assert run["status"] == "completed"
    artifacts = owner.get(f"/api/projects/{world['project']}/artifacts").json()
    assert [(a["kind"], a["ref_id"], a["stage"], a["status"]) for a in artifacts] == [("research_run", run["id"], "synthesized", "current")]

    _set_stage(world, S.gaps_selected)
    _reenter(world, "retrieved", "New papers found")

    (stale,) = owner.get(f"/api/projects/{world['project']}/artifacts", params={"status": "stale"}).json()
    assert stale["ref_id"] == run["id"] and "New papers found" in stale["stale_reason"]


def test_runs_that_did_not_complete_are_not_artifacts(world):
    owner = world["clients"]["owner"]
    owner.post(f"/api/projects/{world['project']}/research-runs", json={"question": "A long enough question?"})  # no sources

    assert owner.get(f"/api/projects/{world['project']}/artifacts").json() == []
