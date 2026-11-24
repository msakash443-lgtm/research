"""The responsibility matrix (X.226): who is responsible for which day-to-day research work. Pure: no DB/HTTP.

Three things are kept apart on purpose:
* a CRediT contribution (`app.credit`) reports who contributed what to the manuscript, after the fact;
* a `ProjectRole` authorises: who may read, edit, approve or manage in this app;
* a responsibility here says who is expected to do or answer for an area of the work now.
Nothing in the app reads a responsibility to grant or refuse access, and CRediT roles are never derived from it.
"""

from __future__ import annotations

AREAS: tuple[tuple[str, str], ...] = (
    ("scope", "Scope and research questions"),
    ("search", "Search and sources"),
    ("screening", "Screening"),
    ("extraction", "Data extraction"),
    ("analysis", "Analysis and synthesis"),
    ("writing", "Writing"),
    ("data_management", "Data management"),
    ("ethics", "Ethics and compliance"),
    ("submission", "Submission and correspondence"),
)
LEVELS: tuple[str, ...] = ("lead", "support")

_AREA_CODES = {code for code, _ in AREAS}


class InvalidResponsibility(ValueError):
    """The assignment can't be stored; the message says why."""


def validate_assignments(area: str, assignments: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """`(user_id, level)` pairs for one area: a known area and level, each person once, at most one lead.
    Returned lead first, then support, each in the order given."""
    if area not in _AREA_CODES:
        raise InvalidResponsibility(f"Unknown area '{area}'. Use one of: {', '.join(sorted(_AREA_CODES))}")
    seen: set[str] = set()
    for user_id, level in assignments:
        if level not in LEVELS:
            raise InvalidResponsibility(f"Level must be one of: {', '.join(LEVELS)}")
        if user_id in seen:
            raise InvalidResponsibility("A person can hold an area once: lead or support, not both.")
        seen.add(user_id)
    if sum(1 for _, level in assignments if level == "lead") > 1:
        raise InvalidResponsibility("An area has at most one lead, so one person answers for it.")
    return sorted(assignments, key=lambda pair: pair[1] != "lead")
