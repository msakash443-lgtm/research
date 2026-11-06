"""The research-path guide (X.55): plain-language meaning of every stage, gate and exit condition, plus advice-only
review types and synthesis methods, loaded from `guides/journey.toml` (config, not code, like `discipline.py`).

`next_steps` turns the real exit conditions of a project's next moves into ranked suggestions. It decides nothing:
the conditions come from `exit_conditions.py`, and advancing still goes through `stage_machine.advance_stage`.
A guide that does not cover every stage, gate and condition fails loudly at load, not with a blank screen later.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from app import stage_order
from app.discipline import KNOWN_REPORTING_GUIDELINES
from app.exit_conditions import RULES, Condition, Level
from app.models import GateCode, ProjectStage

GUIDE_PATH = Path(__file__).resolve().parent / "guides" / "journey.toml"

# Mirrors web/js/workflow.js STEPS: a guide entry may only send the scholar to a screen that exists.
UI_STEPS = frozenset(
    {"overview", "guide", "scope", "criteria", "search", "results", "sources", "screen", "ask", "write", "approvals", "team", "activity"}
)
GLOBAL_CONDITION_IDS = ("EC-0.2", "EC-0.3", "EC-0.4")


class GuideError(RuntimeError):
    pass


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StageGuide(_Strict):
    title: str
    meaning: str
    advice: str
    step: str | None = None  # the screen where the work toward this stage happens; None = no screen yet


class GateGuide(_Strict):
    title: str
    meaning: str
    advice: str


class ConditionGuide(_Strict):
    meaning: str
    step: str | None = None
    built: bool = True  # False for rows exit_conditions reports as "not built yet"; never suggested


class ReviewPath(_Strict):
    id: str
    title: str
    summary: str
    choose_when: list[str]
    you_need: list[str]
    synthesis: str
    reporting_guideline: str | None = None
    in_this_app: str


class PathAnswer(_Strict):
    label: str
    points_to: list[str]


class PathQuestion(_Strict):
    id: str
    text: str
    answers: list[PathAnswer]


class SynthesisMethod(_Strict):
    id: str
    title: str
    what: str
    advisable_when: list[str]
    not_advisable_when: list[str]
    available: bool
    in_this_app: str


class Guide(_Strict):
    version: int
    stages: dict[str, StageGuide]
    gates: dict[str, GateGuide]
    conditions: dict[str, ConditionGuide]
    paths: list[ReviewPath]
    questions: list[PathQuestion]
    synthesis_methods: list[SynthesisMethod]


def _same_keys(name: str, what: str, found: set[str], expected: set[str]) -> None:
    missing, unknown = sorted(expected - found), sorted(found - expected)
    if missing or unknown:
        raise GuideError(f"Guide {name}: {what} missing {missing}, unknown {unknown}")


def _unique(name: str, what: str, ids: list[str]) -> None:
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise GuideError(f"Guide {name}: duplicate {what} ids {duplicates}")


@lru_cache(maxsize=None)
def _load(path: str) -> Guide:
    name = Path(path).name
    try:
        guide = Guide(**tomllib.loads(Path(path).read_text(encoding="utf-8")))
    except Exception as exc:  # unreadable file, TOML syntax, missing or unknown keys
        raise GuideError(f"Guide {name} is invalid: {exc}") from exc

    if guide.version < 1:
        raise GuideError(f"Guide {name} needs a version of 1 or more")
    _same_keys(name, "stages", set(guide.stages), {stage.value for stage in stage_order.chain()})
    _same_keys(name, "gates", set(guide.gates), {code.value for code in GateCode})
    _same_keys(name, "conditions", set(guide.conditions), set(GLOBAL_CONDITION_IDS) | {rule.id for rule in RULES})

    steps = [(key, entry.step) for key, entry in (*guide.stages.items(), *guide.conditions.items())]
    bad_steps = sorted(key for key, step in steps if step is not None and step not in UI_STEPS)
    if bad_steps:
        raise GuideError(f"Guide {name}: unknown screen named by {bad_steps}")

    path_ids = [p.id for p in guide.paths]
    method_ids = [m.id for m in guide.synthesis_methods]
    _unique(name, "path", path_ids)
    _unique(name, "question", [q.id for q in guide.questions])
    _unique(name, "synthesis method", method_ids)
    for review in guide.paths:
        if review.synthesis not in method_ids:
            raise GuideError(f"Guide {name}: path {review.id!r} names unknown synthesis method {review.synthesis!r}")
        if review.reporting_guideline is not None and review.reporting_guideline not in KNOWN_REPORTING_GUIDELINES:
            raise GuideError(f"Guide {name}: path {review.id!r} names unknown guideline {review.reporting_guideline!r}")
    for question in guide.questions:
        if len(question.answers) < 2:
            raise GuideError(f"Guide {name}: question {question.id!r} needs at least two answers")
        for answer in question.answers:
            unknown = [p for p in answer.points_to if p not in path_ids]
            if not answer.points_to or unknown:
                raise GuideError(f"Guide {name}: question {question.id!r} answer points to unknown paths {unknown or '[]'}")
    return guide


def load_guide() -> Guide:
    return _load(str(GUIDE_PATH))


@dataclass(frozen=True)
class Suggestion:
    target: ProjectStage
    condition_id: str
    level: Level
    message: str
    fix: str | None
    meaning: str | None  # None only for a condition the guide does not describe (load_guide rejects that for RULES)
    step: str | None


_LEVEL_RANK = {Level.required: 0, Level.recommended: 1, Level.information: 2}


def next_steps(options: list[tuple[ProjectStage, list[Condition]]], guide: Guide) -> list[Suggestion]:
    """Unmet, built exit conditions of every next move, most binding first; a condition shared by two moves appears once.

    A condition the guide does not describe is still suggested, without a meaning: advice must never hide a real
    unmet condition or take down GET /stage.
    """
    seen: set[str] = set()
    found: list[Suggestion] = []
    for target, conditions in options:
        for condition in conditions:
            entry = guide.conditions.get(condition.id)
            if condition.met or (entry is not None and not entry.built) or condition.id in seen:
                continue
            seen.add(condition.id)
            found.append(
                Suggestion(
                    target=target,
                    condition_id=condition.id,
                    level=condition.level,
                    message=condition.message,
                    fix=condition.fix,
                    meaning=entry.meaning if entry else None,
                    step=entry.step if entry else None,
                )
            )
    return sorted(found, key=lambda s: _LEVEL_RANK[s.level])  # stable: rule order kept within a level
