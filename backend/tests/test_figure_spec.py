"""Figure spec normalization, diff and hashing (plan M8.3): pure functions, no database."""

import pytest

from app import figure_spec
from app.figure_spec import CONCEPTUAL_FRAMEWORK, METHODOLOGY_FLOWCHART, SpecError


def _node(node_id, label="Node", role="independent"):
    return {"id": node_id, "label": label, "role": role, "evidence": []}


def _edge(source, target, kind="direct", label=""):
    return {"from": source, "to": target, "kind": kind, "label": label, "evidence": []}


def test_normalize_trims_text_and_keeps_the_first_of_duplicate_edges():
    spec = {
        "nodes": [_node("a", "  Motivation  "), _node("b")],
        "edges": [_edge("a", "b", label=" leads to "), _edge("a", "b", label="leads to"), _edge("a", "b", label="other")],
    }

    clean = figure_spec.normalize(spec, CONCEPTUAL_FRAMEWORK)

    assert clean["nodes"][0]["label"] == "Motivation"
    assert [edge["label"] for edge in clean["edges"]] == ["leads to", "other"]


def test_normalize_refuses_a_control_character_in_a_label():
    spec = {"nodes": [_node("a", "Bad\x07label")], "edges": []}

    with pytest.raises(SpecError):
        figure_spec.normalize(spec, CONCEPTUAL_FRAMEWORK)


def test_normalize_refuses_two_nodes_with_the_same_id():
    spec = {"nodes": [_node("a"), _node("a", "Other")], "edges": []}

    with pytest.raises(SpecError, match="duplicate node id"):
        figure_spec.normalize(spec, CONCEPTUAL_FRAMEWORK)


def test_normalize_refuses_a_role_that_the_figure_kind_does_not_allow():
    spec = {"nodes": [_node("a", role="mediator")], "edges": []}

    with pytest.raises(SpecError):
        figure_spec.normalize(spec, METHODOLOGY_FLOWCHART)


def test_normalize_refuses_a_node_id_outside_the_allowed_characters():
    spec = {"nodes": [_node("a b")], "edges": []}

    with pytest.raises(SpecError):
        figure_spec.normalize(spec, CONCEPTUAL_FRAMEWORK)


def test_normalize_refuses_more_nodes_than_the_cap_allows():
    spec = {"nodes": [_node(f"n{i}") for i in range(figure_spec.MAX_NODES + 1)], "edges": []}

    with pytest.raises(SpecError, match="at most"):
        figure_spec.normalize(spec, CONCEPTUAL_FRAMEWORK)


def test_normalize_keeps_an_edge_that_points_at_a_missing_node_so_validation_can_flag_it():
    spec = {"nodes": [_node("a")], "edges": [_edge("a", "ghost")]}

    clean = figure_spec.normalize(spec, CONCEPTUAL_FRAMEWORK)

    assert [(edge["from"], edge["to"]) for edge in clean["edges"]] == [("a", "ghost")]


def test_spec_hash_ignores_whitespace_but_changes_with_content():
    base = {"nodes": [_node("a", "Motivation")], "edges": []}
    padded = {"nodes": [_node("a", "  Motivation ")], "edges": []}
    changed = {"nodes": [_node("a", "Motive")], "edges": []}

    assert figure_spec.spec_hash(base, CONCEPTUAL_FRAMEWORK) == figure_spec.spec_hash(padded, CONCEPTUAL_FRAMEWORK)
    assert figure_spec.spec_hash(base, CONCEPTUAL_FRAMEWORK) != figure_spec.spec_hash(changed, CONCEPTUAL_FRAMEWORK)


def test_diff_reports_added_removed_and_changed_nodes_and_edges():
    before = figure_spec.normalize(
        {"nodes": [_node("a", "Old"), _node("b")], "edges": [_edge("a", "b")]}, CONCEPTUAL_FRAMEWORK
    )
    after = figure_spec.normalize(
        {"nodes": [_node("a", "New"), _node("c")], "edges": [_edge("a", "c")]}, CONCEPTUAL_FRAMEWORK
    )

    changes = figure_spec.diff(before, after)

    assert changes["added_nodes"] == ["c"]
    assert changes["removed_nodes"] == ["b"]
    assert changes["changed_nodes"] == ["a"]
    assert changes["added_edges"] == ["a -> c [direct] "]
    assert changes["removed_edges"] == ["a -> b [direct] "]
