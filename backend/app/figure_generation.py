"""Research figures (plan M8.4, M8.5): the pure half of figure generation (conceptual framework and flowchart).

The model sees the uploaded paper's text, ordered so the sections most likely to state the figure come first (see `PRIORITY`), capped and fenced
as untrusted data. Its reply is accepted only if it normalizes as a spec, does not leak the prompt, and every
evidence quote is found in the paper's own text. Otherwise the caller stores nothing.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass

from app import answer_guard, figure_checks, figure_spec
from app.fulltext import FullText
from app.untrusted_text import clean_untrusted

MAX_PASSAGE_CHARS = 24_000
# Canonical section names (fulltext.py) the figure is most likely drawn from, most useful first; the rest follow in order.
# "procedure", "design" and "sample" fold into "methods"; "analysis" is "results".
PRIORITY = {
    figure_spec.CONCEPTUAL_FRAMEWORK: ("methods", "theory", "hypotheses"),
    figure_spec.METHODOLOGY_FLOWCHART: ("methods", "results"),
}
PROMPTS = {
    figure_spec.CONCEPTUAL_FRAMEWORK: ("figure_conceptual_framework", 1),
    figure_spec.METHODOLOGY_FLOWCHART: ("figure_methodology_flowchart", 1),
}


class FigureGenerationError(Exception):
    """A reply that cannot become a stored proposal. `code` is the stable audit code."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


@dataclass(frozen=True)
class Fenced:
    block: str
    nonce: str


def _spans(full: FullText) -> list[tuple[str, int, int]]:
    """(section name, start, end) covering the whole text: a preamble before the first heading, then each section."""
    sections = sorted(full.sections, key=lambda section: section.start)
    if not sections:
        return [("", 0, len(full.text))]
    spans = [("", 0, sections[0].start)] if sections[0].start > 0 else []
    for index, section in enumerate(sections):
        end = sections[index + 1].start if index + 1 < len(sections) else len(full.text)
        spans.append((section.name, section.start, end))
    return spans


def _rank(name: str, kind: str) -> int:
    order = PRIORITY[kind]
    return order.index(name) if name in order else len(order)


def passages(full: FullText, kind: str) -> str:
    """The paper's text, priority sections first, joined and capped at `MAX_PASSAGE_CHARS`."""
    ordered = sorted(enumerate(_spans(full)), key=lambda item: (_rank(item[1][0], kind), item[0]))
    parts: list[str] = []
    total = 0  # characters in `parts` plus their "\n\n" separators, so the joined text stays within the cap
    for _, (_, start, end) in ordered:
        chunk = full.text[start:end].strip()
        separator = 2 if parts else 0
        room = MAX_PASSAGE_CHARS - total - separator
        if not chunk or room <= 0:
            continue
        piece = chunk[:room]
        parts.append(piece)
        total += separator + len(piece)
    return "\n\n".join(parts)


def fence(text: str) -> Fenced:
    """The passages as one untrusted block with a per-call nonce, so the paper cannot close the fence itself."""
    nonce = secrets.token_hex(6)
    cleaned = clean_untrusted(text, MAX_PASSAGE_CHARS).text
    block = f"<<<SOURCE id={nonce}>>>\n{cleaned}\n<<<END SOURCE id={nonce}>>>"
    return Fenced(block=block, nonce=nonce)


def unsupported_quotes(spec: dict, full: FullText) -> list[str]:
    """Every evidence quote on a node or edge that does not occur in the paper's text."""
    missing: list[str] = []
    for item in [*spec["nodes"], *spec["edges"]]:
        for evidence in item["evidence"]:
            if not full.find_quote(evidence["quote"]):
                missing.append(evidence["quote"])
    return missing


def check_reply(reply: object, full: FullText, *, kind: str, system: str, nonce: str) -> dict:
    """The normalized spec for a reply, or `FigureGenerationError` with the code the audit records."""
    try:
        spec = figure_spec.normalize(reply, kind)
    except figure_spec.SpecError as exc:
        raise FigureGenerationError("invalid_spec", str(exc)) from exc
    if answer_guard.check_answer(json.dumps(reply, sort_keys=True), system_prompt=system, nonce=nonce):
        raise FigureGenerationError("leaked_prompt", "the reply repeated the instructions")
    missing = unsupported_quotes(spec, full)
    if missing:
        raise FigureGenerationError("unsupported_element", f"{len(missing)} quote(s) are not in the paper")
    if kind == figure_spec.METHODOLOGY_FLOWCHART:
        structural = figure_checks.structural_errors(figure_checks.check_figure(spec, full, kind))
        if structural:
            raise FigureGenerationError("invalid_structure", f"{len(structural)} structural problem(s)")
    return spec
