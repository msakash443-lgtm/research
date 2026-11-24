"""DOT generation, Graphviz invocation and SVG safety checks (plan M8.3)."""

import re

import pytest

from app import figure_render, figure_spec
from app.config import get_settings
from tests.adversarial_fixtures import FIXTURES, REAL_FINDING

CONCEPTUAL = figure_spec.CONCEPTUAL_FRAMEWORK
FLOWCHART = figure_spec.METHODOLOGY_FLOWCHART

# Each fixture text starts with a shared real finding; the attack part is what would sit in a node label.
HOSTILE_LABELS = [
    (fixture["id"], fixture["text"].removeprefix(f"{REAL_FINDING} "))
    for fixture in FIXTURES
]
HOSTILE_LABELS = [(fixture_id, text) for fixture_id, text in HOSTILE_LABELS
                  if len(text) <= figure_spec.MAX_NODE_LABEL and "\n" not in text]


def _node(node_id, label=None, role="independent"):
    return {"id": node_id, "label": node_id if label is None else label, "role": role, "evidence": []}


def _spec(label, role="independent"):
    return {"nodes": [_node("a", label, role)], "edges": []}


@pytest.mark.parametrize("label", [text for _, text in HOSTILE_LABELS], ids=[fid for fid, _ in HOSTILE_LABELS])
def test_a_hostile_node_label_stays_inside_its_quoted_dot_string(label):
    dot = figure_render.to_dot(CONCEPTUAL, _spec(label))

    node_lines = [line for line in dot.splitlines() if line.startswith("  n0 [")]
    assert len(node_lines) == 1
    match = re.fullmatch(r'  n0 \[label="((?:[^"\\]|\\.)*)\\n\(independent\)"\];', node_lines[0])
    assert match, node_lines[0]
    assert re.sub(r"\\(.)", r"\1", match.group(1)) == label


def test_to_dot_refuses_an_edge_that_names_a_node_not_in_the_figure():
    spec = {"nodes": [_node("a")], "edges": [{"from": "a", "to": "ghost", "kind": "direct", "label": "", "evidence": []}]}

    with pytest.raises(figure_spec.SpecError, match="not in the figure"):
        figure_render.to_dot(CONCEPTUAL, spec)


def test_to_dot_draws_a_flowchart_start_as_an_oval():
    spec = {"nodes": [_node("s", "Start", "start")], "edges": []}

    dot = figure_render.to_dot(FLOWCHART, spec)

    assert "shape=oval" in dot


def test_render_reports_unavailable_when_the_dot_binary_cannot_start(tmp_path):
    with pytest.raises(figure_render.RendererUnavailable):
        figure_render.render_svg("digraph { a }", dot_path=str(tmp_path / "no-such-dot"), timeout_seconds=5)


@pytest.mark.parametrize(
    "svg",
    [
        b'<svg><script>alert(1)</script></svg>',
        b'<svg onload="steal()"></svg>',
        b'<svg><a href="https://evil.example/x"><text>t</text></a></svg>',
    ],
    ids=["script_element", "event_handler", "external_link"],
)
def test_check_svg_refuses_scripts_event_handlers_and_external_links(svg):
    with pytest.raises(figure_render.RenderFailed):
        figure_render.check_svg(svg)


def test_check_svg_allows_internal_links_and_plain_drawings():
    svg = b'<svg><a href="#n0"><text>n0</text></a></svg>'

    assert figure_render.check_svg(svg) == svg


@pytest.mark.requires_graphviz
def test_a_real_dot_run_returns_an_svg_that_passes_the_safety_check():
    spec = {
        "nodes": [
            _node("s", "Start", "start"),
            _node("p", "Survey 40 clinics", "process"),
            _node("e", "End", "end"),
        ],
        "edges": [
            {"from": "s", "to": "p", "kind": "sequence", "label": "", "evidence": []},
            {"from": "p", "to": "e", "kind": "sequence", "label": "", "evidence": []},
        ],
    }

    svg = figure_render.render_svg(
        figure_render.to_dot(FLOWCHART, spec),
        dot_path=get_settings().graphviz_dot_path,
        timeout_seconds=get_settings().figure_render_timeout_seconds,
    )

    assert b"<svg" in svg
    assert b"Survey 40 clinics" in svg
    assert figure_render.check_svg(svg) == svg


@pytest.mark.requires_graphviz
def test_dot_refuses_source_it_cannot_parse_with_a_render_failure():
    with pytest.raises(figure_render.RenderFailed):
        figure_render.render_svg(
            "this is not dot source {{{",
            dot_path=get_settings().graphviz_dot_path,
            timeout_seconds=get_settings().figure_render_timeout_seconds,
        )
