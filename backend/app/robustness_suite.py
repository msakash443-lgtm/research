"""Robustness-suite spec variants over an already-validated analysis spec (M5.4.2, spec 5.9 bullet 3).

Pure: no I/O, no database, no Docker. Callers run each alternative spec through the same sandboxed path as the
original (`app.analysis_runner.run_script`, via a freshly locked `AnalysisPlan`) and compare fingerprints with
`app.robustness.fingerprint_results`. This module only decides which alternative specs are worth trying.
"""

from __future__ import annotations

from typing import Any


def leave_one_out_specs(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """One alternative spec per variable dropped, each labelled by which variable is missing.

    Needs at least two variables to drop one and still have something to analyse. If dropping a variable would
    leave "correlations" with fewer than two variables, that test is dropped from the alternative too (a
    single-variable correlation is never a valid plan, see `app.analysis_plan.validate`); if nothing is left to
    test, that variable has no alternative spec at all.
    """
    variables = spec["variables"]
    if len(variables) < 2:
        return []
    alternatives: list[dict[str, Any]] = []
    for variable in variables:
        remaining = [v for v in variables if v != variable]
        tests = list(spec["tests"])
        if "correlations" in tests and len(remaining) < 2:
            tests = [t for t in tests if t != "correlations"]
        if not tests:
            continue
        alternatives.append({
            "label": f"drop {variable}",
            "spec": {"tests": tests, "variables": remaining, "seed": spec["seed"]},
        })
    return alternatives
