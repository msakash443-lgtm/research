"""Inclusion/exclusion criteria: frameworks, validation and the G2 lock (spec 5.2, 5.3, gate G2).

A review's criteria are organised by a framework (PICO, PICOC, SPIDER, or `custom` for a
discipline's own) and each criterion is either an inclusion or an exclusion rule with a stable
reason code (I1, E2, ...). Screening decisions (M2) and PRISMA counts cite these codes.

Once gate G2 ("approve search strategy + criteria") is approved by a person the criteria are
locked: changing them would let an approval given for one set cover another (plan rule 21). They
unlock only when G2 is pending again, which happens through a re-entry (M0.5.5).
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Gate, GateCode, GateStatus, Project

FRAMEWORKS: dict[str, tuple[str, ...]] = {
    "pico": ("population", "intervention", "comparison", "outcome"),
    "picoc": ("population", "intervention", "comparison", "outcome", "context"),
    "spider": ("sample", "phenomenon_of_interest", "design", "evaluation", "research_type"),
    "custom": (),  # a discipline's own structure: criteria carry no framework element
}
MAX_CRITERIA = 50
MIN_TEXT = 3
MAX_TEXT = 1000
CODE_PREFIX = {"include": "I", "exclude": "E"}
CODE_RE = re.compile(r"^[IE][1-9]\d{0,2}$")


def criteria_locked(db: Session, project: Project) -> bool:
    """True when a person has approved G2, i.e. these criteria are what was approved."""
    status = db.scalar(select(Gate.status).where(Gate.project_id == project.id, Gate.code == GateCode.G2))
    return status == GateStatus.approved


def problems(framework: str | None, criteria: list) -> list[str]:
    """What is still missing before the criteria can reasonably go to G2 (advice, not a block)."""
    found = []
    if framework is None:
        found.append("Choose a framework (PICO, PICOC, SPIDER or custom).")
    if not any(c.kind == "include" for c in criteria):
        found.append("Add at least one inclusion criterion.")
    if not any(c.kind == "exclude" for c in criteria):
        found.append("Add at least one exclusion criterion.")
    if framework in FRAMEWORKS and FRAMEWORKS[framework]:
        covered = {c.element for c in criteria if c.element}
        missing = [e for e in FRAMEWORKS[framework] if e not in covered]
        if missing:
            found.append("No criterion yet for: " + ", ".join(missing) + ".")
    return found
