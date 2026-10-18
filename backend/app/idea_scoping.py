"""Idea scoping for gate G1 (M4.7, spec 5.1): novelty comparators and the feasibility checklist.

Pure: no database, no network, no model. Novelty here is lexical: the share of an idea's content words that a
project source's title and abstract also use. It is not the semantic distance the spec names (that needs
embeddings and is a separate subtask). Every score comes with the comparators and shared terms it was computed
from, so no score is a black box. Feasibility answers are the scholar's own; code only reports what is still open.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from app.note_suggest import tokens as content_tokens

MAX_COMPARATORS = 5
MAX_SHARED_TERMS = 12
FEASIBILITY_ITEMS = ("data_availability", "method_fit", "ethics_risk", "time_and_skills")
FEASIBILITY_STATUSES = ("ok", "concern", "blocker", "unsure")


@dataclass(frozen=True)
class Reference:
    """A project source as the comparison sees it: its id, title, year and title/abstract text."""

    source_id: str
    title: str
    year: int | None
    text: str


@dataclass(frozen=True)
class Comparator:
    source_id: str
    title: str
    year: int | None
    overlap: float  # shared content words / the idea's content words
    shared_terms: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Novelty:
    idea_terms: list[str]
    # 1 - closest overlap. None when the idea has no content words or the project has no sources to compare with.
    distinctiveness: float | None
    comparators: list[Comparator]


def novelty(idea_text: str, references: Iterable[Reference]) -> Novelty:
    idea = content_tokens(idea_text)
    refs = list(references)
    if not idea or not refs:
        return Novelty(sorted(idea), None, [])
    scored: list[Comparator] = []
    for ref in refs:
        shared = idea & content_tokens(f"{ref.title} {ref.text}")
        if not shared:
            continue
        scored.append(
            Comparator(
                source_id=ref.source_id,
                title=ref.title,
                year=ref.year,
                overlap=round(len(shared) / len(idea), 3),
                shared_terms=sorted(shared)[:MAX_SHARED_TERMS],
            )
        )
    scored.sort(key=lambda c: (-c.overlap, c.title.lower(), c.source_id))
    top = scored[:MAX_COMPARATORS]
    distinctiveness = round(1 - top[0].overlap, 3) if top else 1.0
    return Novelty(sorted(idea), distinctiveness, top)


def feasibility_gaps(answers: Mapping[str, str]) -> list[str]:
    """Checklist items with no answer yet, or answered `unsure`, in checklist order. Blockers and concerns are answers."""
    return [item for item in FEASIBILITY_ITEMS if answers.get(item) in (None, "unsure")]
