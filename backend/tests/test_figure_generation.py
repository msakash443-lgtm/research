"""Figure generation, pure half (plan M8.4): passage order and cap, fencing, quote verification, reply refusal."""

import pytest

from app import figure_spec
from app.figure_generation import (
    MAX_PASSAGE_CHARS,
    FigureGenerationError,
    check_reply,
    fence,
    passages,
    unsupported_quotes,
)
from app.fulltext import FullText, Section

SYSTEM = "You extract conceptual frameworks. Reply with JSON only."


def _paper(sections):
    """A FullText whose sections are `(name, body)` pairs laid out in order, with no pages."""
    text = ""
    marks = []
    for name, body in sections:
        marks.append((name, len(text)))
        text += body
    sections_out = tuple(
        Section(title=name.title(), name=name, page=1, start=start) for name, start in marks if name
    )
    return FullText(text=text, pages=(), sections=sections_out, empty_pages=(), sha256="0" * 64)


def _reply(quote, *, label="Trust", role="independent"):
    evidence = [{"quote": quote, "section": None}]
    return {
        "nodes": [
            {"id": "trust", "label": label, "role": role, "evidence": evidence},
            {"id": "collab", "label": "Collaboration", "role": "dependent", "evidence": evidence},
        ],
        "edges": [{"from": "trust", "to": "collab", "kind": "direct", "label": "predicts", "evidence": evidence}],
    }


def test_the_passages_put_methods_then_theory_then_hypotheses_before_the_other_sections():
    full = _paper([
        ("", "Preamble words. "),
        ("results", "Results words. "),
        ("hypotheses", "Hypotheses words. "),
        ("theory", "Theory words. "),
        ("methods", "Methods words. "),
    ])
    text = passages(full, figure_spec.CONCEPTUAL_FRAMEWORK)
    assert text.index("Methods words") < text.index("Theory words") < text.index("Hypotheses words") < text.index("Results words")


def test_the_passages_stop_at_the_passage_limit_and_drop_the_rest():
    full = _paper([("methods", "m" * (MAX_PASSAGE_CHARS - 1000)), ("theory", "t" * 5000)])
    text = passages(full, figure_spec.CONCEPTUAL_FRAMEWORK)
    assert len(text) == MAX_PASSAGE_CHARS
    assert text.count("t") == 998  # two characters of the cap go to the "\n\n" separator


def test_the_fence_brackets_the_text_with_one_nonce_on_both_markers():
    fenced = fence("Trust predicts collaboration.")
    assert fenced.block.startswith(f"<<<SOURCE id={fenced.nonce}>>>")
    assert fenced.block.endswith(f"<<<END SOURCE id={fenced.nonce}>>>")
    assert "Trust predicts collaboration." in fenced.block


def test_a_quote_that_is_not_in_the_paper_is_reported_as_unsupported():
    full = _paper([("methods", "Trust predicts collaboration among partners.")])
    spec = figure_spec.normalize(_reply("Trust predicts collaboration"), figure_spec.CONCEPTUAL_FRAMEWORK)
    assert unsupported_quotes(spec, full) == []
    spec = figure_spec.normalize(_reply("Sample was 900 firms"), figure_spec.CONCEPTUAL_FRAMEWORK)
    assert unsupported_quotes(spec, full) == ["Sample was 900 firms"] * 3


def test_a_reply_whose_quotes_are_all_in_the_paper_is_accepted_as_a_normalized_spec():
    full = _paper([("methods", "Trust predicts collaboration among partners.")])
    spec = check_reply(_reply("Trust predicts collaboration"), full, kind=figure_spec.CONCEPTUAL_FRAMEWORK, system=SYSTEM, nonce="abc123")
    assert [node["id"] for node in spec["nodes"]] == ["trust", "collab"]


def test_a_reply_with_an_invented_quote_is_refused_as_unsupported_element():
    full = _paper([("methods", "Trust predicts collaboration among partners.")])
    with pytest.raises(FigureGenerationError) as exc:
        check_reply(_reply("Sample was 900 firms"), full, kind=figure_spec.CONCEPTUAL_FRAMEWORK, system=SYSTEM, nonce="abc123")
    assert exc.value.code == "unsupported_element"


def test_a_reply_that_repeats_the_fence_nonce_is_refused_as_leaked_prompt():
    full = _paper([("methods", "Trust predicts collaboration among partners.")])
    with pytest.raises(FigureGenerationError) as exc:
        check_reply(_reply("Trust predicts collaboration", label="abc123"), full, kind=figure_spec.CONCEPTUAL_FRAMEWORK, system=SYSTEM, nonce="abc123")
    assert exc.value.code == "leaked_prompt"


def test_a_reply_with_a_role_the_kind_does_not_allow_is_refused_as_invalid_spec():
    full = _paper([("methods", "Trust predicts collaboration among partners.")])
    with pytest.raises(FigureGenerationError) as exc:
        check_reply(_reply("Trust predicts collaboration", role="start"), full, kind=figure_spec.CONCEPTUAL_FRAMEWORK, system=SYSTEM, nonce="abc123")
    assert exc.value.code == "invalid_spec"


def _flow_node(node_id, role):
    return {"id": node_id, "label": node_id.title(), "role": role,
            "evidence": [{"quote": "Trust predicts collaboration", "section": None}]}


def _flow_edge(source, target):
    return {"from": source, "to": target, "kind": "sequence", "label": "",
            "evidence": [{"quote": "Trust predicts collaboration", "section": None}]}


def test_a_flowchart_with_one_start_and_an_end_is_accepted_as_a_normalized_spec():
    full = _paper([("methods", "Trust predicts collaboration among partners.")])
    reply = {"nodes": [_flow_node("s", "start"), _flow_node("p", "process"), _flow_node("end", "end")],
             "edges": [_flow_edge("s", "p"), _flow_edge("p", "end")]}
    spec = check_reply(reply, full, kind=figure_spec.METHODOLOGY_FLOWCHART, system=SYSTEM, nonce="abc123")
    assert [node["id"] for node in spec["nodes"]] == ["s", "p", "end"]


def test_a_flowchart_with_two_starts_is_refused_as_invalid_structure():
    full = _paper([("methods", "Trust predicts collaboration among partners.")])
    reply = {"nodes": [_flow_node("s1", "start"), _flow_node("s2", "start"), _flow_node("end", "end")],
             "edges": [_flow_edge("s1", "end"), _flow_edge("s2", "end")]}
    with pytest.raises(FigureGenerationError) as exc:
        check_reply(reply, full, kind=figure_spec.METHODOLOGY_FLOWCHART, system=SYSTEM, nonce="abc123")
    assert exc.value.code == "invalid_structure"


def test_a_flowchart_with_a_step_the_start_cannot_reach_is_refused_as_invalid_structure():
    full = _paper([("methods", "Trust predicts collaboration among partners.")])
    reply = {"nodes": [_flow_node("s", "start"), _flow_node("end", "end"), _flow_node("stray", "process")],
             "edges": [_flow_edge("s", "end"), _flow_edge("stray", "end")]}
    with pytest.raises(FigureGenerationError) as exc:
        check_reply(reply, full, kind=figure_spec.METHODOLOGY_FLOWCHART, system=SYSTEM, nonce="abc123")
    assert exc.value.code == "invalid_structure"
