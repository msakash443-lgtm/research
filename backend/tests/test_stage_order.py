import re
from pathlib import Path

from app import stage_order
from app.gates import GATE_ORDER, GATE_STAGES
from app.models import GateCode, ProjectStage

APP = Path(__file__).resolve().parents[1] / "app"


def test_chain_is_the_appendix_c_journey_in_enum_order():
    assert stage_order.chain() == list(ProjectStage)


def test_forward_moves_follow_the_chain_and_branches_are_explicit():
    assert stage_order.next_stages(None, ProjectStage.idea) == [ProjectStage.scoped]
    assert stage_order.next_stages(None, ProjectStage.submitted) == [ProjectStage.revision_loop, ProjectStage.accepted]
    assert stage_order.next_stages(None, ProjectStage.revision_loop) == [ProjectStage.accepted]
    assert stage_order.next_stages(None, ProjectStage.accepted) == []


def test_gate_order_is_derived_from_the_stage_each_gate_completes():
    positions = {stage: i for i, stage in enumerate(stage_order.chain())}
    assert GATE_ORDER == sorted(GateCode, key=lambda code: positions[GATE_STAGES[code]])
    assert GATE_ORDER == [GateCode(f"G{n}") for n in range(1, 12)]


def test_no_module_outside_stage_order_iterates_the_stage_enum_for_ordering():
    pattern = re.compile(r"list\(ProjectStage\)|for \w+ in ProjectStage\b|in ProjectStage\)")
    offenders = [
        path.name
        for path in APP.glob("*.py")
        if path.name not in ("stage_order.py", "models.py") and pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
