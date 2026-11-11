"""Deterministic comparison of human-verified key findings across papers (plan M3.7).

A `key_findings` item is "structured" when it carries non-empty `exposure`, `outcome` and `population`
strings plus a `direction` that is `"positive"`, `"negative"`, `"mixed"`, or null (the paper reports no
significant effect). Anything else - including items produced under an older extraction schema that
never had these fields - is skipped and counted in `unstructured_findings` instead of being compared.

Structured findings are grouped by the normalised `(exposure, outcome)` pair, using the same Unicode
normalisation as `dedupe.normalise_title` so wording, case and punctuation differences do not split a
pair that means the same comparison; at most one finding is kept per paper per pair. `mixed` findings
are left out of every comparison (never a conflict, inconsistency or agreement) but still count as
structured, since they do carry the new fields.

Within a group: two different papers reporting opposite effects (`positive` vs `negative`) are a
`conflict`; a paper reporting an effect against a paper reporting no effect (`null`) is `inconsistent`;
two or more different papers reporting the same direction (including two or more `null`s) are an
`agreement`. When the papers being compared have differing non-empty normalised `population` strings,
the pair/group also gets `population_note: "may differ by population"`.
"""

from __future__ import annotations

from app.dedupe import normalise_title

_DIRECTIONS = {"positive", "negative", "mixed"}


def _citation(paper: dict, finding: dict, quote: str) -> dict:
    return {
        "source_id": paper["source_id"], "title": paper["title"], "extraction_id": paper["extraction_id"],
        "quote": quote, "direction": finding.get("direction"), "population": finding.get("population"),
    }


def _structured_findings(matrix: dict) -> tuple[list[dict], int]:
    """Human-verified, well-formed key findings and a count of the ones skipped as unstructured."""
    structured: list[dict] = []
    unstructured = 0
    for paper in matrix["papers"]:
        cell = paper["cells"].get("key_findings", {})
        if not paper["verified_by_human"] or not cell.get("quote"):
            continue
        values = cell.get("value") or []
        if not isinstance(values, list):
            continue
        for finding in values:
            if not isinstance(finding, dict):
                continue
            has_fields = {"exposure", "outcome", "population", "direction"} <= finding.keys()
            exposure, outcome, population, direction = (
                finding.get("exposure"), finding.get("outcome"), finding.get("population"), finding.get("direction")
            )
            valid = (
                has_fields
                and isinstance(exposure, str) and exposure.strip()
                and isinstance(outcome, str) and outcome.strip()
                and isinstance(population, str)
                and (direction is None or isinstance(direction, str) and direction in _DIRECTIONS)
            )
            if not valid:
                unstructured += 1
                continue
            key = (normalise_title(exposure), normalise_title(outcome))
            if not key[0] or not key[1]:
                unstructured += 1
                continue
            structured.append({
                "key": key, "exposure": exposure, "outcome": outcome, "direction": direction,
                "citation": _citation(paper, finding, cell["quote"]),
            })
    return structured, unstructured


def _population_note(citations: list[dict]) -> str | None:
    populations = {
        normalise_title(c["population"]) for c in citations if isinstance(c["population"], str) and c["population"].strip()
    }
    return "may differ by population" if len(populations) > 1 else None


def _group(exposure: str, outcome: str, members: list[dict]) -> dict:
    citations = [member["citation"] for member in members]
    group = {"exposure": exposure, "outcome": outcome, "findings": citations}
    note = _population_note(citations)
    if note:
        group["population_note"] = note
    return group


def detect(matrix: dict) -> dict:
    """Compare human-verified `key_findings` across papers; see the module docstring for the rules.

    `matrix` is an evidence-matrix dict (`{"papers": [...]}`, as built by `evidence_matrix.build`).
    Returns `{"conflicts": [...], "inconsistent": [...], "agreements": [...], "unstructured_findings": N}`.
    """
    structured, unstructured = _structured_findings(matrix)
    by_key: dict[tuple[str, str], dict[str, dict]] = {}
    for finding in structured:
        if finding["direction"] == "mixed":
            continue
        per_paper = by_key.setdefault(finding["key"], {})
        per_paper.setdefault(finding["citation"]["source_id"], finding)

    conflicts: list[dict] = []
    inconsistent: list[dict] = []
    agreements: list[dict] = []
    for members in by_key.values():
        papers = list(members.values())
        exposure, outcome = papers[0]["exposure"], papers[0]["outcome"]
        positives = [p for p in papers if p["direction"] == "positive"]
        negatives = [p for p in papers if p["direction"] == "negative"]
        nulls = [p for p in papers if p["direction"] is None]
        for a in positives:
            for b in negatives:
                conflicts.append(_group(exposure, outcome, [a, b]))
        for a in positives + negatives:
            for b in nulls:
                inconsistent.append(_group(exposure, outcome, [a, b]))
        for same_direction in (positives, negatives, nulls):
            if len(same_direction) >= 2:
                agreements.append(_group(exposure, outcome, same_direction))
    return {
        "conflicts": conflicts, "inconsistent": inconsistent, "agreements": agreements,
        "unstructured_findings": unstructured,
    }
