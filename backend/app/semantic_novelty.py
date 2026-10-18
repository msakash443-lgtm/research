"""Semantic novelty for an idea (M4.7.b, spec 5.1.3): cosine similarity to the project's sources, comparators shown.

Pure: no network, no database, no model. The caller supplies the vectors from the configured embedding model; this
module only compares them. Distinctiveness is 1 minus the highest similarity to any source, and every score comes
with the comparators it was computed from. A zero or mismatched vector is refused, never replaced (plan rule 22).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.idea_scoping import Reference

MAX_COMPARATORS = 5


class VectorError(ValueError):
    pass


@dataclass(frozen=True)
class Comparator:
    source_id: str
    title: str
    year: int | None
    similarity: float


@dataclass(frozen=True)
class SemanticNovelty:
    # 1 - highest similarity. None when the project has no sources to compare with.
    distinctiveness: float | None
    comparators: list[Comparator] = field(default_factory=list)


def _norm(vector: list[float]) -> float:
    if not vector or not all(math.isfinite(x) for x in vector):
        raise VectorError("A vector is empty or contains a non-finite number.")
    size = math.sqrt(sum(x * x for x in vector))
    if size == 0:
        raise VectorError("A vector is all zeros and can't be compared.")
    return size


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise VectorError("Vectors have different lengths and can't be compared.")
    return sum(x * y for x, y in zip(a, b)) / (_norm(a) * _norm(b))


def semantic_novelty(idea_vector: list[float], references: list[tuple[Reference, list[float]]]) -> SemanticNovelty:
    if not references:
        _norm(idea_vector)  # still refuse an unusable idea vector
        return SemanticNovelty(None, [])
    scored = [
        Comparator(ref.source_id, ref.title, ref.year, round(cosine(idea_vector, vector), 3))
        for ref, vector in references
    ]
    scored.sort(key=lambda c: (-c.similarity, c.title.lower(), c.source_id))
    top = scored[:MAX_COMPARATORS]
    return SemanticNovelty(round(1 - top[0].similarity, 3), top)
