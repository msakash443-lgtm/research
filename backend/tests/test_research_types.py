"""Plan X.43.1: research types are validated stage plans (Q22, X.40 decisions 4 and 5), not code."""

import pytest

from app import research_types as rt
from app import stage_order
from app.models import GateCode, ProjectStage

S = ProjectStage


@pytest.fixture
def type_dir(tmp_path, monkeypatch):
    """Point the loader at an empty directory so a test can write its own research-type files."""
    monkeypatch.setattr(rt, "TYPE_DIR", tmp_path)
    rt._load.cache_clear()
    yield tmp_path
    rt._load.cache_clear()


# The literature-review chain, as a list of (stage, gate) TOML lines a test can edit.
LIT_REVIEW = [
    ("idea", None), ("scoped", "G1"), ("design_approved", "G7"), ("search_planned", "G2"), ("retrieved", None),
    ("screened", "G3"), ("extracted", "G4"), ("synthesized", None), ("gaps_selected", "G5"), ("drafted", "G10"),
    ("revised", None), ("submission_ready", "G11"), ("submitted", None), ("revision_loop", None), ("accepted", None),
]


def _toml(stages, name="demo", extra=""):
    lines = [f'name = "{name}"', "version = 1", 'label = "Demo"', 'description = "A demo type."',
             "human_participants = false", extra]
    for stage, gate in stages:
        lines.append("[[stages]]")
        lines.append(f'stage = "{stage}"')
        if gate:
            lines.append(f'gate = "{gate}"')
    return "\n".join(lines) + "\n"


def _write(directory, stages, name="demo", extra=""):
    (directory / f"{name}.toml").write_text(_toml(stages, name, extra), encoding="utf-8")


# ---- the shipped types -------------------------------------------------------------------------

def test_the_shipped_types_are_the_three_approved_ones_and_all_load():
    assert rt.available_research_types() == ["full_journey", "literature_review", "qualitative"]
    for name in rt.available_research_types():
        loaded = rt.load_research_type(name)
        assert loaded.name == name and loaded.version >= 1


def test_full_journey_is_todays_exact_chain_so_existing_projects_do_not_change():
    full = rt.load_research_type("full_journey")
    assert full.chain == stage_order.chain()
    assert full.gates == list(GateCode)
    assert all(step.label is None for step in full.stages)
    assert full.human_participants is True


@pytest.mark.parametrize("name", ["full_journey", "literature_review", "qualitative"])
def test_every_shipped_type_keeps_the_five_mandatory_gates(name):
    assert set(rt.MANDATORY_GATES) <= set(rt.load_research_type(name).gates)


def test_literature_review_drops_its_four_stages_and_moves_design_after_scoping():
    lit = rt.load_research_type("literature_review")
    assert lit.chain == [S(stage) for stage, _ in LIT_REVIEW]
    for dropped in (S.framework, S.data_collected, S.plan_locked, S.analyzed):
        assert dropped not in lit.chain
    assert lit.gates == [GateCode(g) for g in ("G1", "G7", "G2", "G3", "G4", "G5", "G10", "G11")]
    assert lit.human_participants is False


def test_qualitative_drops_only_plan_locked_and_relabels_three_stages():
    qual = rt.load_research_type("qualitative")
    assert qual.chain == [s for s in stage_order.chain() if s != S.plan_locked]
    assert GateCode.G8 not in qual.gates
    labels = {step.stage: step.label for step in qual.stages if step.label}
    assert labels == {
        S.framework: "Sensitising concepts & questions",
        S.design_approved: "Design, interview guide & ethics",
        S.analyzed: "Themes & interpretation",
    }


# ---- what a type file may not do ---------------------------------------------------------------

def test_a_valid_custom_file_loads(type_dir):
    _write(type_dir, LIT_REVIEW)
    assert rt.load_research_type("demo").chain[2] == S.design_approved


@pytest.mark.parametrize("dropped", ["search_planned", "screened", "extracted", "design_approved", "submission_ready"])
def test_dropping_a_mandatory_gate_stage_fails_loudly(type_dir, dropped):
    _write(type_dir, [(s, g) for s, g in LIT_REVIEW if s != dropped])
    with pytest.raises(rt.ResearchTypeError, match="mandatory gate"):
        rt.load_research_type("demo")


def test_a_kept_stage_cannot_lose_its_gate(type_dir):
    _write(type_dir, [(s, None if s == "scoped" else g) for s, g in LIT_REVIEW])
    with pytest.raises(rt.ResearchTypeError, match="G1.*scoped"):
        rt.load_research_type("demo")


def test_a_gate_cannot_move_to_another_stage(type_dir):
    _write(type_dir, [(s, "G6" if s == "scoped" else g) for s, g in LIT_REVIEW])
    with pytest.raises(rt.ResearchTypeError, match="scoped"):
        rt.load_research_type("demo")


def test_a_gate_cannot_be_put_on_an_ungated_stage(type_dir):
    _write(type_dir, [(s, "G9" if s == "retrieved" else g) for s, g in LIT_REVIEW])
    with pytest.raises(rt.ResearchTypeError, match="retrieved"):
        rt.load_research_type("demo")


def test_unknown_or_repeated_stages_are_rejected(type_dir):
    _write(type_dir, LIT_REVIEW[:3] + [("brainstormed", None)] + LIT_REVIEW[3:])
    with pytest.raises(rt.ResearchTypeError, match="invalid"):
        rt.load_research_type("demo")
    rt._load.cache_clear()
    _write(type_dir, LIT_REVIEW[:5] + [("retrieved", None)] + LIT_REVIEW[5:])
    with pytest.raises(rt.ResearchTypeError, match="more than once"):
        rt.load_research_type("demo")


def test_the_chain_must_start_at_idea(type_dir):
    _write(type_dir, LIT_REVIEW[1:])
    with pytest.raises(rt.ResearchTypeError, match="start at 'idea'"):
        rt.load_research_type("demo")


def test_the_submission_branch_stays_at_the_end_in_order(type_dir):
    tail_swapped = LIT_REVIEW[:-3] + [("submitted", None), ("accepted", None), ("revision_loop", None)]
    _write(type_dir, tail_swapped)
    with pytest.raises(rt.ResearchTypeError, match="end with"):
        rt.load_research_type("demo")


def test_unknown_keys_missing_fields_and_bad_versions_are_rejected(type_dir):
    _write(type_dir, LIT_REVIEW, extra="colour = 'blue'")
    with pytest.raises(rt.ResearchTypeError, match="invalid"):
        rt.load_research_type("demo")
    rt._load.cache_clear()
    (type_dir / "demo.toml").write_text(_toml(LIT_REVIEW).replace("version = 1", "version = 0"), encoding="utf-8")
    with pytest.raises(rt.ResearchTypeError, match="version"):
        rt.load_research_type("demo")
    rt._load.cache_clear()
    (type_dir / "demo.toml").write_text(_toml(LIT_REVIEW).replace("human_participants = false\n", ""), encoding="utf-8")
    with pytest.raises(rt.ResearchTypeError, match="invalid"):
        rt.load_research_type("demo")


def test_name_must_match_the_file_and_unknown_types_fail(type_dir):
    (type_dir / "demo.toml").write_text(_toml(LIT_REVIEW, name="other"), encoding="utf-8")
    with pytest.raises(rt.ResearchTypeError, match="declares name"):
        rt.load_research_type("demo")
    with pytest.raises(rt.ResearchTypeError, match="does not exist"):
        rt.load_research_type("nope")
    with pytest.raises(rt.ResearchTypeError, match="does not exist"):
        rt.load_research_type("../etc/passwd")
