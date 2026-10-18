"""Pure helpers for robustness checks and re-run identity (M5.4.1).

No I/O, no database, no model. Callers pass numbers in and get numbers back.
"""

import hashlib
import json
import math


def holm_adjust(p_values: list[float]) -> list[float]:
    """Holm step-down adjusted p-values, in the input order.

    Each adjusted value is at least as large as the one before it in rank order
    and is capped at 1.0, so the procedure never reports a smaller p than a
    less significant test.
    """
    for p in p_values:
        # bool is an int subclass; True would otherwise be read as a p-value of 1 (codex nit, M5.4.1)
        if isinstance(p, bool) or not (isinstance(p, (int, float)) and 0.0 <= p <= 1.0) or math.isnan(p):
            raise ValueError(f"p-value out of range: {p!r}")
    m = len(p_values)
    order = sorted(range(m), key=lambda i: p_values[i])
    adjusted = [0.0] * m
    running_max = 0.0
    for rank, idx in enumerate(order):
        candidate = (m - rank) * p_values[idx]
        running_max = max(running_max, candidate)
        adjusted[idx] = min(1.0, running_max)
    return adjusted


def _normalise(value):
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise ValueError("non-finite number cannot be fingerprinted")
        rounded = float(f"{value:.12g}")
        # 0.0, -0.0 and 1.0 hash as 0, 0 and 1, so the same reported number has one canonical form (codex nit, M5.4.1)
        if rounded.is_integer() and abs(rounded) < 2**53:
            return int(rounded)
        return rounded
    if isinstance(value, int):
        return value
    if isinstance(value, dict):
        # str() would merge 1 and "1" into one key, so only string keys are accepted (codex nit, M5.4.1)
        if not all(isinstance(k, str) for k in value):
            raise TypeError("result keys must be strings")
        return {k: _normalise(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalise(v) for v in value]
    raise TypeError(f"cannot fingerprint {type(value).__name__}")


def fingerprint_results(results: dict) -> str:
    """SHA-256 of a canonical JSON form of analysis results.

    Floats are rounded to 12 significant digits before hashing, so differences
    far below that precision do not change the fingerprint, while a change
    visible at 12 significant digits does. A difference that falls on a rounding
    boundary can still change it, so this is not a guarantee for every last-bit change.
    """
    canonical = json.dumps(_normalise(results), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
