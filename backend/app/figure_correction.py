"""Correction of a figure version that fails its deterministic checks (plan M8.7). Pure: no DB, no network.

The model's corrected spec is accepted for storage only when `check_figure` reports no ERROR-severity finding.
"""

from __future__ import annotations

import json
from typing import Any

from app import figure_checks

PROMPT = ("figure_correction", 1)
CODE = "correction_still_invalid"


class CorrectionError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def draft(spec: dict[str, Any], findings: list[figure_checks.Finding]) -> str:
    """The draft figure and the findings it fails, as the prompt's `draft` placeholder."""
    lines = [json.dumps(spec, sort_keys=True), "", "Checks it fails:"]
    if not findings:
        lines.append("none")
    for finding in findings:
        lines.append(f"- {finding.code} ({finding.element_id}): {finding.message}")
    return "\n".join(lines)


def verify(spec: dict[str, Any], full: Any, kind: str) -> None:
    """Raise `CorrectionError` unless the corrected spec has no ERROR-severity finding against the paper's text."""
    errors = [
        finding for finding in figure_checks.check_figure(spec, full, kind)
        if finding.severity == figure_checks.ERROR
    ]
    if errors:
        codes = ", ".join(sorted({finding.code for finding in errors}))
        raise CorrectionError(CODE, f"corrected figure still fails checks: {codes}")
