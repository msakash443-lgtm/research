import pytest

from app import qual_coding as qc


def _book():
    return qc.new_codebook({"trust": "belief in others", "cost": "perceived cost"})


def test_accept_adds_a_logged_code():
    state = qc.accept(_book(), "fear", "anxiety about outcome", actor="coder1")
    assert state["codes"]["fear"] == "anxiety about outcome"
    assert state["log"][-1] == {"op": "accept", "code": "fear", "actor": "coder1"}


def test_accept_refuses_duplicates_and_unnamed_actor():
    with pytest.raises(ValueError):
        qc.accept(_book(), "trust", "again", actor="coder1")
    with pytest.raises(ValueError):
        qc.accept(_book(), "fear", "x", actor="  ")


def test_rename_keeps_old_label_resolvable():
    state = qc.rename(_book(), "trust", "institutional trust", actor="coder1")
    assert "trust" not in state["codes"]
    assert qc.resolve(state, "trust") == "institutional trust"
    assert state["log"][-1]["op"] == "rename"


def test_merge_moves_labels_and_logs_the_target():
    state = qc.merge(_book(), "cost", "trust", actor="coder2")
    assert "cost" not in state["codes"]
    assert qc.resolve(state, "cost") == "trust"
    assert state["log"][-1] == {"op": "merge", "from": "cost", "into": "trust", "definition": "perceived cost",
                                "actor": "coder2"}


def test_merge_logs_the_dropped_definition_so_nothing_is_lost_silently():
    # review defect (10): the source's definition was deleted and only its name was logged
    state = qc.merge(_book(), "cost", "trust", actor="coder2")
    entry = state["log"][-1]
    assert entry["definition"] == "perceived cost"
    assert entry["from"] == "cost" and entry["into"] == "trust"


def test_merge_refuses_self_unknown_and_missing_actor():
    with pytest.raises(ValueError):
        qc.merge(_book(), "trust", "trust", actor="c")
    with pytest.raises(ValueError):
        qc.merge(_book(), "nope", "trust", actor="c")
    with pytest.raises(ValueError):
        qc.merge(_book(), "cost", "trust", actor="")


def test_resolve_follows_chains_and_refuses_unknown_labels():
    state = qc.rename(_book(), "trust", "t2", actor="c")
    state = qc.merge(state, "t2", "cost", actor="c")
    assert qc.resolve(state, "trust") == "cost"
    with pytest.raises(ValueError):
        qc.resolve(state, "ghost")


def test_kappa_perfect_agreement_is_one():
    assert qc.cohen_kappa(["a", "b", "a"], ["a", "b", "a"]) == pytest.approx(1.0)


def test_kappa_matches_hand_computed_value():
    # observed 3/4; marginals a: 2 and 2 -> expected (2*2 + 2*2)/16 = 0.5
    # kappa = (0.75 - 0.5) / (1 - 0.5) = 0.5
    assert qc.cohen_kappa(["a", "a", "b", "b"], ["a", "a", "b", "a"]) == pytest.approx(0.5)


def test_kappa_single_category_is_perfect_not_division_by_zero():
    assert qc.cohen_kappa(["x", "x"], ["x", "x"]) == 1.0


def test_kappa_input_checks():
    with pytest.raises(ValueError):
        qc.cohen_kappa(["a"], ["a", "b"])
    with pytest.raises(ValueError):
        qc.cohen_kappa([], [])
