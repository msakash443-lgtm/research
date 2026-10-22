"""Pure submission lifecycle and configuration gates for M7.5."""
from __future__ import annotations

STATUSES = ("draft", "submitted", "under_review", "revision_requested", "accepted", "rejected", "withdrawn")
TRANSITIONS = {"draft": {"submitted", "withdrawn"}, "submitted": {"under_review", "withdrawn"},
               "under_review": {"revision_requested", "accepted", "rejected", "withdrawn"},
               "revision_requested": {"submitted", "withdrawn"}, "accepted": set(), "rejected": set(), "withdrawn": set()}


def transition(current: str, target: str) -> str:
    if current not in STATUSES or target not in STATUSES or target not in TRANSITIONS[current]:
        raise ValueError(f"invalid submission transition: {current} -> {target}")
    return target


def integration_enabled(settings: dict, name: str) -> bool:
    return bool(settings.get(f"{name}_enabled", False))
