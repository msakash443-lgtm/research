"""Deterministic figure checks (plan M8.6): one test per finding code, plus the flowchart structure rules (M8.5)."""

from app import figure_checks, figure_spec
from app.fulltext import FullText

PAPER = "Trust predicts collaboration among partners. Sample was 900 firms."


def _paper():
    return FullText(text=PAPER, pages=(), sections=(), empty_pages=(), sha256="0" * 64)


def _node(node_id, role, label=None, quote="Trust predicts collaboration"):
    return {"id": node_id, "label": label or node_id.title(), "role": role,
            "evidence": [{"quote": quote, "section": None}]}


def _edge(source, target, kind="direct", label="", quote="Trust predicts collaboration"):
    return {"from": source, "to": target, "kind": kind, "label": label,
            "evidence": [{"quote": quote, "section": None}]}


def _findings(spec, kind=figure_spec.CONCEPTUAL_FRAMEWORK):
    return {(f.code, f.element_id): f for f in figure_checks.check_figure(spec, _paper(), kind)}


def _flow(nodes, edges):
    return {"nodes": nodes, "edges": edges}


def test_a_clean_framework_whose_quotes_all_appear_in_the_paper_has_no_findings():
    spec = {"nodes": [_node("trust", "independent"), _node("collab", "dependent")],
            "edges": [_edge("trust", "collab", label="predicts")]}
    assert figure_checks.check_figure(spec, _paper(), figure_spec.CONCEPTUAL_FRAMEWORK) == []


def test_a_quote_that_is_not_in_the_paper_is_an_error_on_its_element():
    spec = {"nodes": [_node("trust", "independent", quote="Income was measured yearly"),
                      _node("collab", "dependent")],
            "edges": [_edge("trust", "collab")]}
    finding = _findings(spec)[("quote_not_found", "trust")]
    assert finding.severity == figure_checks.ERROR


def test_an_edge_to_a_node_that_is_not_in_the_figure_is_a_dangling_edge_error():
    spec = {"nodes": [_node("trust", "independent"), _node("collab", "dependent")],
            "edges": [_edge("trust", "collab"), _edge("trust", "ghost")]}
    finding = _findings(spec)[("dangling_edge", "trust->ghost")]
    assert finding.severity == figure_checks.ERROR


def test_a_node_no_edge_touches_is_an_orphan_warning_not_an_error():
    spec = {"nodes": [_node("trust", "independent"), _node("collab", "dependent"), _node("lonely", "control")],
            "edges": [_edge("trust", "collab")]}
    finding = _findings(spec)[("orphan_node", "lonely")]
    assert finding.severity == figure_checks.WARNING


def test_a_mediation_edge_with_no_mediator_node_is_an_error_on_that_edge():
    spec = {"nodes": [_node("trust", "independent"), _node("collab", "dependent")],
            "edges": [_edge("trust", "collab", kind="mediation")]}
    finding = _findings(spec)[("mediation_without_via", "trust->collab")]
    assert finding.severity == figure_checks.ERROR


def test_a_mediation_edge_is_accepted_once_the_figure_has_a_mediator_node():
    spec = {"nodes": [_node("trust", "independent"), _node("norms", "mediator"), _node("collab", "dependent")],
            "edges": [_edge("trust", "norms"), _edge("norms", "collab", kind="mediation")]}
    assert not any(code == "mediation_without_via" for code, _ in _findings(spec))


def test_a_label_over_the_readable_length_is_a_warning_on_its_node():
    long_label = "x" * (figure_checks.READABLE_NODE_LABEL + 1)
    spec = {"nodes": [_node("trust", "independent", label=long_label), _node("collab", "dependent")],
            "edges": [_edge("trust", "collab")]}
    finding = _findings(spec)[("label_too_long", "trust")]
    assert finding.severity == figure_checks.WARNING


def test_a_flowchart_with_two_start_nodes_names_each_start_as_an_error():
    spec = _flow(
        [_node("s1", "start"), _node("s2", "start"), _node("end", "end")],
        [_edge("s1", "end", kind="sequence"), _edge("s2", "end", kind="sequence")],
    )
    found = _findings(spec, figure_spec.METHODOLOGY_FLOWCHART)
    assert found[("multiple_starts", "s1")].severity == figure_checks.ERROR
    assert found[("multiple_starts", "s2")].severity == figure_checks.ERROR


def test_a_flowchart_with_no_start_node_is_an_error_without_an_element():
    spec = _flow([_node("p", "process"), _node("end", "end")], [_edge("p", "end", kind="sequence")])
    assert ("no_start", None) in _findings(spec, figure_spec.METHODOLOGY_FLOWCHART)


def test_a_flowchart_with_no_end_node_is_an_error_without_an_element():
    spec = _flow([_node("s", "start"), _node("p", "process")], [_edge("s", "p", kind="sequence")])
    assert ("no_end", None) in _findings(spec, figure_spec.METHODOLOGY_FLOWCHART)


def test_a_flowchart_node_that_cannot_be_reached_from_the_start_is_an_error_on_that_node():
    spec = _flow(
        [_node("s", "start"), _node("end", "end"), _node("stray", "process")],
        [_edge("s", "end", kind="sequence"), _edge("stray", "end", kind="sequence")],
    )
    assert _findings(spec, figure_spec.METHODOLOGY_FLOWCHART)[("unreachable_node", "stray")].severity == figure_checks.ERROR


def test_a_well_formed_flowchart_has_no_structural_errors():
    spec = _flow(
        [_node("s", "start"), _node("p", "process"), _node("end", "end")],
        [_edge("s", "p", kind="sequence"), _edge("p", "end", kind="sequence")],
    )
    assert figure_checks.structural_errors(
        figure_checks.check_figure(spec, _paper(), figure_spec.METHODOLOGY_FLOWCHART)
    ) == []


def test_a_long_label_on_a_flowchart_step_is_a_warning_and_not_a_structural_error():
    long_label = "x" * (figure_checks.READABLE_NODE_LABEL + 1)
    spec = _flow(
        [_node("s", "start"), _node("p", "process", label=long_label), _node("end", "end")],
        [_edge("s", "p", kind="sequence"), _edge("p", "end", kind="sequence")],
    )
    assert figure_checks.structural_errors(
        figure_checks.check_figure(spec, _paper(), figure_spec.METHODOLOGY_FLOWCHART)
    ) == []
