"""Plan M0.5.9: after a re-entry a project can't move into or past a stage whose results are still stale
until each one is replaced (Decisions log 2026-10-07: a current artifact of the same kind, produced at the
same stage, registered no earlier than the stale one went stale)."""

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import artifacts as art
from app import stage_machine as sm
from app.artifacts import StageError
from app.database import SessionLocal
from app.main import app
from app.models import Artifact, ArtifactStatus, AuditEvent, Gate, GateStatus, Project, ProjectStage as S, utcnow


@pytest.fixture
def world():
    tag = uuid.uuid4().hex[:8]
    client = TestClient(app)
    owner_id = client.post(
        "/api/auth/development/login", json={"email": f"stale-{tag}@example.com", "display_name": "S"}
    ).json()["id"]
    project_id = client.post("/api/projects", json={"title": "Stale guard"}).json()["id"]
    return {"client": client, "project": project_id, "pid": uuid.UUID(project_id), "owner_id": owner_id}


def _set_stage(world, stage):
    with SessionLocal() as db:
        db.get(Project, world["pid"]).stage = stage
        db.commit()


def _approve_all_gates(world):
    with SessionLocal() as db:
        project = db.get(Project, world["pid"])
        for gate in art.ensure_gates(db, project):
            gate.status, gate.decided_by, gate.decided_at = GateStatus.approved, world["owner_id"], utcnow()
        db.commit()


def _artifact(world, kind, stage, ref=None):
    with SessionLocal() as db:
        artifact = art.register_artifact(db, world["pid"], kind, ref or uuid.uuid4().hex, stage, actor="tester")
        db.commit()
        return artifact.id


def _reenter(world, stage):
    response = world["client"].post(
        f"/api/projects/{world['project']}/stage/reenter", json={"stage": stage, "reason": "rethink"}
    )
    assert response.status_code == 200, response.text


def _advance(world):
    return world["client"].post(f"/api/projects/{world['project']}/stage/advance", json={})


def _stage(world):
    return world["client"].get(f"/api/projects/{world['project']}").json()["stage"]


@pytest.fixture
def after_reentry(world):
    """A project that reached `synthesized` with a synthesis run, then went back to `extracted`."""
    _set_stage(world, S.synthesized)
    world["run"] = _artifact(world, "research_run", S.synthesized)
    _reenter(world, "extracted")
    _approve_all_gates(world)  # gates are not what this file tests
    return world


def test_advancing_into_a_stage_with_an_unreplaced_stale_result_is_refused(after_reentry):
    world = after_reentry

    response = _advance(world)  # extracted -> synthesized

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "stale" in detail["message"] and "'synthesized'" in detail["message"]
    assert [a["id"] for a in detail["stale_artifacts"]] == [str(world["run"])]
    assert detail["stale_artifacts"][0]["status"] == "stale"
    assert _stage(world) == "extracted"


def test_a_refused_advance_changes_nothing_and_is_not_audited(after_reentry):
    world = after_reentry
    with SessionLocal() as db:
        before = db.scalar(select(AuditEvent.id).where(AuditEvent.action == "project.advanced", AuditEvent.project_id == world["pid"]))

    _advance(world)

    with SessionLocal() as db:
        assert db.get(Artifact, world["run"]).status == ArtifactStatus.stale
        assert db.scalar(select(AuditEvent.id).where(AuditEvent.action == "project.advanced", AuditEvent.project_id == world["pid"])) == before


def test_a_new_result_of_the_same_kind_at_that_stage_replaces_it(after_reentry):
    world = after_reentry
    _artifact(world, "research_run", S.synthesized)  # a fresh synthesis run

    response = _advance(world)

    assert response.status_code == 200, response.text
    assert _stage(world) == "synthesized"
    with SessionLocal() as db:
        assert db.get(Artifact, world["run"]).status == ArtifactStatus.stale  # the old one stays, flagged


def test_regenerating_the_same_record_also_clears_the_block(after_reentry):
    world = after_reentry
    with SessionLocal() as db:
        art.refresh_artifact(db, db.get(Artifact, world["run"]))
        db.commit()

    assert _advance(world).status_code == 200


@pytest.mark.parametrize(
    "kind,stage",
    [("search_log", S.synthesized), ("research_run", S.gaps_selected)],
    ids=["other-kind-same-stage", "same-kind-other-stage"],
)
def test_a_result_of_another_kind_or_stage_is_not_a_replacement(after_reentry, kind, stage):
    world = after_reentry
    _artifact(world, kind, stage)

    assert _advance(world).status_code == 409


def test_a_current_result_older_than_the_staleness_is_not_a_replacement(world):
    _set_stage(world, S.synthesized)
    stale_id = _artifact(world, "research_run", S.synthesized)
    older_id = _artifact(world, "research_run", S.synthesized)
    with SessionLocal() as db:
        # `older` was made an hour before `stale` went stale and is (somehow) still current.
        stale = db.get(Artifact, stale_id)
        stale.status, stale.stale_reason, stale.stale_at = ArtifactStatus.stale, "r", utcnow()
        db.get(Artifact, older_id).created_at = utcnow() - timedelta(hours=1)
        project = db.get(Project, world["pid"])
        project.stage = S.extracted
        assert [a.id for a in art.unreplaced_stale(db, project, S.synthesized)] == [stale_id]
        db.rollback()


def test_stale_results_of_later_stages_do_not_block_earlier_steps(world):
    _set_stage(world, S.gaps_selected)
    _artifact(world, "gap_list", S.gaps_selected)
    _reenter(world, "retrieved")
    _approve_all_gates(world)

    assert _advance(world).status_code == 200  # retrieved -> screened
    assert _stage(world) == "screened"


def test_moving_past_a_stage_is_refused_too(world):
    """A stale result left behind at an earlier stage still blocks every later step."""
    _set_stage(world, S.screened)
    left_behind = _artifact(world, "screening_set", S.screened)
    _reenter(world, "retrieved")
    _approve_all_gates(world)
    _set_stage(world, S.screened)  # e.g. moved on before this rule existed

    response = _advance(world)  # screened -> extracted

    assert response.status_code == 409
    assert [a["id"] for a in response.json()["detail"]["stale_artifacts"]] == [str(left_behind)]


def test_the_stage_view_shows_the_next_stage_as_not_ready(after_reentry):
    world = after_reentry

    body = world["client"].get(f"/api/projects/{world['project']}/stage").json()

    assert body["next"] == [{"stage": "synthesized", "gate": None, "gate_status": None, "ready": False}]
    _artifact(world, "research_run", S.synthesized)
    assert world["client"].get(f"/api/projects/{world['project']}/stage").json()["next"][0]["ready"] is True


def test_system_producers_are_held_by_the_same_rule(after_reentry):
    world = after_reentry
    with SessionLocal() as db:
        project = db.get(Project, world["pid"])
        with pytest.raises(sm.StaleResults) as blocked:
            sm.advance_stage(db, project, actor="agent:search")
        blocking = [a.id for a in blocked.value.artifacts]
        db.rollback()
    assert isinstance(blocked.value, StageError)
    assert blocking == [world["run"]]
    assert _stage(world) == "extracted"


def test_the_check_runs_before_the_gate_check(world):
    """A stage with both problems names the stale work first: redo the work, then a person approves."""
    _set_stage(world, S.screened)
    _artifact(world, "extraction_set", S.extracted)
    _reenter(world, "retrieved")
    _set_stage(world, S.screened)
    with SessionLocal() as db:
        assert {g.status for g in db.scalars(select(Gate).where(Gate.project_id == world["pid"]))} == {GateStatus.pending}

    response = _advance(world)  # screened -> extracted, G4 pending and a stale extraction set

    assert response.status_code == 409 and "stale_artifacts" in response.json()["detail"]
