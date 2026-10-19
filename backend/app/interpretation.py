"""Findings-to-literature comparison (M6.9, spec 5.10 and 8). Pure: plain facts in, a structured
agree/disagree/unclear classification out, no database or network.

An `AnalysisFinding` is one named numeric result read from a locked `AnalysisRun`'s stored output
(M5.3). A `LiteratureValue` is one numeric value a human-verified `Extraction` reported for the same
named field, from one source paper (M3.5's evidence table/evidence matrix). `classify` compares the
finding's value against every literature value recorded for the same field and reports whether the
finding's direction agrees with, disagrees with, or is unclear against the body of prior literature.

This module makes no claim about substantive correctness and never asks a model to judge agreement
(research-integrity rule: an AI never silently decides whether a finding "really" agrees with prior
work). The comparison is a deterministic sign check over numbers that already exist in the database;
`app.interpretation_map` is the DB-aware layer that fetches those numbers, persists the classification
as an append-only row, and tracks whether a person has confirmed it (G9, spec 5.10/8). `draft_content`
below builds a ProseMirror discussion-section draft whose only citations are `stat`/`cite` nodes (the
same node shapes `app.number_citation` and `app.claim_linker` already use): the draft never types a
substantive number or claim as free prose, so there is nothing in it for a person to blindly trust.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Any


class Agreement(str, Enum):
    agree = "agree"
    disagree = "disagree"
    unclear = "unclear"


@dataclass(frozen=True)
class LiteratureValue:
    """One numeric value a human-verified extraction reported for `field_key`, from one source paper."""

    source_id: str
    extraction_id: str
    field_key: str
    value: float


@dataclass(frozen=True)
class AnalysisFinding:
    """One named numeric result read from a locked analysis run's stored output. `value` is `None` when
    the run has no numeric output at this path (which is itself reported, as `unclear`)."""

    run_id: str
    output_key: str
    path: str
    value: float | None


@dataclass(frozen=True)
class InterpretationItem:
    finding: AnalysisFinding
    classification: Agreement
    literature_source_ids: tuple[str, ...]
    literature_extraction_ids: tuple[str, ...]
    rationale: str


def _sign(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def classify(finding: AnalysisFinding, literature: Iterable[LiteratureValue]) -> InterpretationItem:
    """Classify one analysis finding against every literature value sharing its `output_key`.

    - `unclear`: the finding has no numeric value, no literature value shares its key, either side's
      value is exactly zero (direction cannot be compared), or the literature values disagree with each
      other on direction.
    - `agree`: every matching literature value has the same sign as the finding.
    - `disagree`: every matching literature value has the opposite sign of the finding.
    """
    if finding.value is None:
        return InterpretationItem(
            finding, Agreement.unclear, (), (), "The analysis run reported no numeric value for this finding."
        )
    matches = [value for value in literature if value.field_key == finding.output_key]
    if not matches:
        return InterpretationItem(
            finding, Agreement.unclear, (), (), f"No prior literature value is recorded for '{finding.output_key}'."
        )
    source_ids = tuple(sorted({value.source_id for value in matches}))
    extraction_ids = tuple(sorted({value.extraction_id for value in matches}))
    finding_sign = _sign(finding.value)
    signs = {_sign(value.value) for value in matches}
    if finding_sign == 0 or signs == {0}:
        return InterpretationItem(
            finding, Agreement.unclear, source_ids, extraction_ids,
            "The finding's value or every matching literature value is exactly zero, so direction cannot be compared.",
        )
    if signs == {finding_sign}:
        return InterpretationItem(
            finding, Agreement.agree, source_ids, extraction_ids,
            f"The finding's direction matches {len(matches)} prior literature value(s) from {len(source_ids)} source(s).",
        )
    if signs == {-finding_sign}:
        return InterpretationItem(
            finding, Agreement.disagree, source_ids, extraction_ids,
            f"The finding's direction is opposite to {len(matches)} prior literature value(s) from {len(source_ids)} source(s).",
        )
    return InterpretationItem(
        finding, Agreement.unclear, source_ids, extraction_ids,
        "Prior literature values disagree with each other on direction, so no single comparison applies.",
    )


def build_map(findings: Iterable[AnalysisFinding], literature: Iterable[LiteratureValue]) -> list[InterpretationItem]:
    """Classify every finding against the same literature values, in the given order."""
    lit = list(literature)
    return [classify(finding, lit) for finding in findings]


_LEAD_IN = {
    Agreement.agree: "This finding agrees with prior literature:",
    Agreement.disagree: "This finding disagrees with prior literature:",
    Agreement.unclear: "This finding's relationship to prior literature is unclear:",
}


def draft_content(items: Iterable[InterpretationItem]) -> dict[str, Any]:
    """A discussion-section draft as ProseMirror content: one paragraph per finding, citing its analysis
    run with a `stat` node and every literature source it was compared against with `cite` nodes. No
    substantive number or claim is ever typed as free prose; a person must accept, edit or discard the
    whole thing like any other AI suggestion (`app.section_draft`'s pattern), and this function itself
    never calls a model."""
    paragraphs: list[dict[str, Any]] = []
    for item in items:
        children: list[dict[str, Any]] = [
            {"type": "text", "text": f"{_LEAD_IN[item.classification]} "},
            {"type": "stat", "attrs": {"analysis_run_id": item.finding.run_id, "path": item.finding.path}},
        ]
        for source_id in item.literature_source_ids:
            children.append({"type": "text", "text": " "})
            children.append({"type": "cite", "attrs": {"source_id": source_id}})
        children.append({"type": "text", "text": f" {item.rationale}"})
        paragraphs.append({"type": "paragraph", "content": children})
    if not paragraphs:
        paragraphs = [{"type": "paragraph", "content": []}]
    return {"type": "doc", "content": paragraphs}
