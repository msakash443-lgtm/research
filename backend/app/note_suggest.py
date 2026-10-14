"""Non-AI triage suggestions for an inbox note (X.31.2).

Pure: no database, no network, no model. A note's score against a target is the share of the note's distinct
content words that the target contains, so 0.15 means "at least 15% of what the note says appears in it".
A project is suggested at 0.15 or more (the best one only), a source at 0.20 or more (up to three), and the
suggested project's stage is offered as a chip. Nothing here leaves the server.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

PROJECT_THRESHOLD = 0.15
SOURCE_THRESHOLD = 0.20
MAX_SOURCES = 3

STOP_WORDS = frozenset(
    """a an and are as at be been but by can could did do does for from had has have how i if in into is it its
    may me more most my no not of on or our so such than that the their then there these they this those to too
    under up us was we were what when where which who why will with would you your""".split()
)


@dataclass(frozen=True)
class ProjectSummary:
    id: str
    title: str
    description: str
    stage: str
    question_texts: tuple[str, ...]


@dataclass(frozen=True)
class SourceSummary:
    id: str
    project_id: str
    title: str


@dataclass(frozen=True)
class Suggestion:
    kind: str  # "project" | "source" | "stage"
    target_id: str
    label: str
    score: float
    project_id: str | None = None


def tokens(text: str) -> frozenset[str]:
    """Lowercased words, with stop words and single characters dropped."""
    words = re.findall(r"\w+", text.lower())
    return frozenset(word for word in words if len(word) > 1 and word not in STOP_WORDS)


def _score(note_tokens: frozenset[str], target_tokens: frozenset[str]) -> float:
    return len(note_tokens & target_tokens) / len(note_tokens)


def suggest(note_text: str, projects: Iterable[ProjectSummary], sources: Iterable[SourceSummary]) -> list[Suggestion]:
    note_tokens = tokens(note_text)
    if not note_tokens:
        return []

    project_scores = []
    for project in projects:
        target = tokens(" ".join([project.title, project.description, *project.question_texts]))
        score = _score(note_tokens, target)
        if score >= PROJECT_THRESHOLD:
            project_scores.append((score, project))
    # Best score first; the id breaks ties so the same input always gives the same output.
    project_scores.sort(key=lambda pair: (-pair[0], pair[1].id))

    result: list[Suggestion] = []
    if project_scores:
        score, best = project_scores[0]
        result.append(Suggestion(kind="project", target_id=best.id, label=best.title, score=round(score, 3)))
        result.append(Suggestion(kind="stage", target_id=best.id, label=best.stage, score=round(score, 3)))

    source_scores = []
    for source in sources:
        score = _score(note_tokens, tokens(source.title))
        if score >= SOURCE_THRESHOLD:
            source_scores.append((score, source))
    source_scores.sort(key=lambda pair: (-pair[0], pair[1].id))
    for score, source in source_scores[:MAX_SOURCES]:
        result.append(
            Suggestion(kind="source", target_id=source.id, label=source.title, score=round(score, 3), project_id=source.project_id)
        )
    return result
