"""Deviation log against the pre-registered analysis plan (M5.8.2, spec 5.9).

Pure: no database, no model, nothing stored. Compares the locked, pre-registered spec with the spec actually run and
lists every difference. The app never decides a deviation is acceptable: each one needs a reason written by the
researcher, and a log with an unexplained deviation is refused rather than filled in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MAX_REASON = 2000


class DeviationError(ValueError):
    """A deviation log that cannot be built; the message is shown to the researcher."""


@dataclass(frozen=True)
class Deviation:
    key: str  # stable id, e.g. "test_added:correlations", used to attach a reason
    kind: str
    detail: str
    reason: str = ""


def _names(spec: dict[str, Any], field: str) -> list[str]:
    value = spec.get(field)
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise DeviationError(f"The plan has no valid {field!r} list.")
    return value


def find_deviations(
    planned: dict[str, Any], actual: dict[str, Any], planned_dataset_sha256: str, actual_dataset_sha256: str
) -> list[Deviation]:
    """Every difference between the pre-registered plan and what was run, in a fixed order."""
    found: list[Deviation] = []
    for field, label in (("tests", "test"), ("variables", "variable")):
        before, after = _names(planned, field), _names(actual, field)
        for name in before:
            if name not in after:
                found.append(Deviation(f"{label}_removed:{name}", f"{label}_removed", f"Planned {label} {name!r} was not run."))
        for name in after:
            if name not in before:
                found.append(Deviation(f"{label}_added:{name}", f"{label}_added", f"{label.capitalize()} {name!r} was run but not planned."))
    if planned.get("seed") != actual.get("seed"):
        found.append(Deviation("seed_changed", "seed_changed", f"Seed changed from {planned.get('seed')!r} to {actual.get('seed')!r}."))
    if planned_dataset_sha256 != actual_dataset_sha256:
        found.append(Deviation("dataset_changed", "dataset_changed", "The analysis ran on a different dataset version than the plan was locked on."))
    return found


def build_log(
    planned: dict[str, Any],
    actual: dict[str, Any],
    planned_dataset_sha256: str,
    actual_dataset_sha256: str,
    reasons: dict[str, str],
) -> list[Deviation]:
    """The deviation log with the researcher's reasons attached. Refuses a missing or blank reason, and a reason that
    names no deviation (so a typo cannot leave the real deviation looking explained)."""
    found = find_deviations(planned, actual, planned_dataset_sha256, actual_dataset_sha256)
    keys = {d.key for d in found}
    unknown = sorted(set(reasons) - keys)
    if unknown:
        raise DeviationError(f"Reason given for something that is not a deviation: {', '.join(unknown)}.")
    log = []
    for d in found:
        reason = " ".join((reasons.get(d.key) or "").split())
        if not reason:
            raise DeviationError(f"Write a reason for the deviation {d.key!r}; none is filled in for you.")
        if len(reason) > MAX_REASON:
            raise DeviationError(f"The reason for {d.key!r} is longer than {MAX_REASON} characters.")
        log.append(Deviation(d.key, d.kind, d.detail, reason))
    return log
