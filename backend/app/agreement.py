"""Inter-rater agreement for dual screening (M2.4.2). Pure functions: plain data in, plain data out.

Cohen's kappa compares two reviewers on the same items beyond the agreement chance would give.
Reported honestly: kappa is `None` (not 1.0, not 0.0) when it is undefined, and the raw percent
agreement is always given next to it, because kappa misleads when almost every item has the same
label ("kappa paradox").
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Hashable, Iterable, Sequence

# Landis & Koch (1977) bands, the usual wording for a kappa value.
_BANDS = ((0.0, "poor"), (0.20, "slight"), (0.40, "fair"), (0.60, "moderate"), (0.80, "substantial"), (1.0, "almost perfect"))


@dataclass(frozen=True)
class Agreement:
    n: int  # items both reviewers decided
    agreed: int
    percent_agreement: float | None  # None when n == 0
    kappa: float | None  # None when undefined (no items, or both reviewers used one single label)
    band: str | None
    categories: tuple[str, ...]
    matrix: dict[str, dict[str, int]]  # matrix[reviewer_a_label][reviewer_b_label] = count
    note: str | None = None  # why kappa is None, or a warning about reading it


def _band(kappa: float) -> str:
    if kappa < 0:
        return "worse than chance"
    for upper, name in _BANDS:
        if kappa <= upper:
            return name
    return "almost perfect"


def cohen_kappa(pairs: Iterable[tuple[Hashable, Hashable]]) -> Agreement:
    """Agreement of two reviewers over `(label_a, label_b)` pairs, one per item both decided."""
    items = [(str(a), str(b)) for a, b in pairs]
    n = len(items)
    categories = tuple(sorted({label for pair in items for label in pair}))
    matrix = {a: {b: 0 for b in categories} for a in categories}
    for a, b in items:
        matrix[a][b] += 1
    if n == 0:
        return Agreement(0, 0, None, None, None, categories, matrix, note="No item has been decided by both reviewers yet.")
    agreed = sum(matrix[c][c] for c in categories)
    observed = agreed / n
    expected = sum((sum(matrix[c].values()) / n) * (sum(matrix[r][c] for r in categories) / n) for c in categories)
    if expected >= 1.0:
        return Agreement(n, agreed, observed, None, None, categories, matrix,
                         note="Kappa is undefined: both reviewers used a single label for every item.")
    kappa = (observed - expected) / (1 - expected)
    note = None
    if n < 30:
        note = f"Only {n} items: treat kappa as a rough guide."
    if observed >= 0.9 and kappa < 0.6:
        note = (note + " " if note else "") + "Agreement is high but kappa is low because nearly all items share one label."
    return Agreement(n, agreed, observed, kappa, _band(kappa), categories, matrix, note)


def conflicts(decisions: dict[Hashable, tuple[str, str]]) -> list[Hashable]:
    """Item ids where the two reviewers' labels differ, in the order given."""
    return [item for item, (a, b) in decisions.items() if a != b]


def meets_threshold(kappa: float | None, threshold: float) -> bool:
    """True only for a defined kappa at or above the threshold; an undefined kappa never passes."""
    return kappa is not None and kappa >= threshold
