"""Plan X.173-X.175: the quantitative_survey, experimental and mixed_methods research types (decision X.172)."""

import uuid

import pytest
from sqlalchemy import select

from app import research_types, stage_machine, stage_order
from app.database import SessionLocal
from app.models import Gate, GateCode, GateStatus, Project, ProjectStage

from tests.api_helpers import sign_in_user
from tests.test_research_types_api import _advance_to_g7, _team_project

S = ProjectStage
NEW_TYPES = ["quantitative_survey", "experimental", "mixed_methods"]
FULL = stage_order.chain()


@pytest.mark.parametrize("name", NEW_TYPES)
def test_new_type_loads_with_every_mandatory_gate_and_human_participants(name):
    loaded = research_types.load_research_type(name)
    assert loaded.name == name and loaded.version == 1
    assert set(research_types.MANDATORY_GATES) <= set(loaded.gates)
    assert loaded.human_participants is True


def test_survey_keeps_the_full_chain_and_every_gate_but_relabels_stages():
    loaded = research_types.load_research_type("quantitative_survey")
    assert loaded.chain == FULL
    assert loaded.gates == list(GateCode)
    assert any(step.label for step in loaded.stages)


def test_experimental_locks_the_plan_before_data_collection_and_keeps_every_gate():
    loaded = research_types.load_research_type("experimental")
    expected = list(FULL)
    i = expected.index(S.data_collected)
    expected[i], expected[i + 1] = expected[i + 1], expected[i]
    assert loaded.chain == expected
    assert loaded.chain.index(S.plan_locked) < loaded.chain.index(S.data_collected)
    assert loaded.gates == list(GateCode)
    assert any(step.label for step in loaded.stages)


def test_experimental_keeps_the_locked_plan_and_analysis_stages():
    loaded = research_types.load_research_type("experimental")
    assert {S.plan_locked, S.analyzed} <= set(loaded.chain)
    assert {GateCode.G8, GateCode.G9} <= set(loaded.gates)


def test_mixed_methods_differs_from_full_journey_by_dropping_gap_selection_only():
    loaded = research_types.load_research_type("mixed_methods")
    assert loaded.chain == [stage for stage in FULL if stage != S.gaps_selected]
    assert GateCode.G5 not in loaded.gates
    assert {GateCode.G6, GateCode.G8, GateCode.G9} <= set(loaded.gates)


def test_catalogue_lists_the_new_types_with_their_stages_and_gates():
    client, _ = sign_in_user(f"new-types-catalogue-{uuid.uuid4().hex[:8]}@example.com")
    rows = {row["name"]: row for row in client.get("/api/research-types").json()}
    assert set(NEW_TYPES) <= rows.keys()
    for name in NEW_TYPES:
        gates = [step["gate"] for step in rows[name]["stages"] if step["gate"]]
        assert {"G2", "G3", "G4", "G7", "G11"} <= set(gates)


@pytest.mark.parametrize("name", NEW_TYPES)
def test_choosing_the_type_snapshots_its_chain_into_stage_plan_json(name):
    world = _team_project()
    assert world["owner"].put(f"/api/projects/{world['project']}/research-type", json={"name": name}).status_code == 200
    with SessionLocal() as db:
        project = db.get(Project, uuid.UUID(world["project"]))
        assert project.research_type == name
        assert [step["stage"] for step in project.stage_plan_json["stages"]] == [s.value for s in research_types.load_research_type(name).chain]
    chain = world["owner"].get(f"/api/projects/{world['project']}/stage").json()["chain"]
    assert [item["stage"] for item in chain] == [s.value for s in research_types.load_research_type(name).chain]


def test_advance_stage_refuses_a_stage_outside_the_chain_and_skipping_ahead():
    world = _team_project()
    assert world["owner"].put(f"/api/projects/{world['project']}/research-type", json={"name": "mixed_methods"}).status_code == 200
    with SessionLocal() as db:
        project = db.get(Project, uuid.UUID(world["project"]))
        project.stage = S.synthesized
        with pytest.raises(stage_machine.StageError):
            stage_machine.advance_stage(db, project, S.gaps_selected, actor="test")
        with pytest.raises(stage_machine.StageError):
            stage_machine.advance_stage(db, project, S.design_approved, actor="test")
        assert stage_order.next_stages(project, S.synthesized) == [S.framework]


@pytest.mark.parametrize("name", NEW_TYPES)
def test_g7_needs_no_no_participants_confirmation_because_the_type_has_participants(name):
    world = _team_project()
    _advance_to_g7(world, name)
    with SessionLocal() as db:
        gate = db.scalar(select(Gate).where(Gate.project_id == uuid.UUID(world["project"]), Gate.code == GateCode.G7))
        assert gate.status == GateStatus.pending
    approved = world["owner"].post(f"/api/projects/{world['project']}/gates/G7/approve", json={})
    assert approved.status_code == 200
    assert approved.json()["note"] is None
