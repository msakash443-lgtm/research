"""State rules for AI suggestions (X.46.1). Pure: plain data in, plain data out, no DB or HTTP.

A suggestion has no stored status. Its state is computed from its append-only event list, so the state can
never drift from the history. A suggestion changes nothing by itself: it counts as a decision only after a
person's `accepted` / `edited_and_accepted` event, and an AI suggestion can never target a gate.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

ACCEPTED = "accepted"
EDITED_AND_ACCEPTED = "edited_and_accepted"
DISCARDED = "discarded"
RESTORED = "restored"
UNDONE = "undone"
REASKED = "reasked"
LOCKED = "locked"
EVENT_KINDS = (ACCEPTED, EDITED_AND_ACCEPTED, DISCARDED, RESTORED, UNDONE, REASKED, LOCKED)

PENDING_STATE = "pending"
ACCEPTED_STATE = "accepted"
DISCARDED_STATE = "discarded"
SUPERSEDED_STATE = "superseded"  # replaced by an "ask again" child
LOCKED_STATE = "locked"  # "never for this item"

# state -> {event kind -> next state}. Anything not listed is refused.
TRANSITIONS: dict[str, dict[str, str]] = {
    PENDING_STATE: {
        ACCEPTED: ACCEPTED_STATE,
        EDITED_AND_ACCEPTED: ACCEPTED_STATE,
        DISCARDED: DISCARDED_STATE,
        REASKED: SUPERSEDED_STATE,
        LOCKED: LOCKED_STATE,
    },
    ACCEPTED_STATE: {UNDONE: PENDING_STATE},
    DISCARDED_STATE: {RESTORED: PENDING_STATE, LOCKED: LOCKED_STATE},
    SUPERSEDED_STATE: {},
    LOCKED_STATE: {},
}

# A suggestion may never target an approval: gates are decided by a person on the gate itself.
FORBIDDEN_TARGET_KINDS = frozenset({"gate", "gate_decision", "approval", "stage"})

MAX_FINAL_TEXT_CHARS = 20000


class InvalidTransition(ValueError):
    pass


class ForbiddenTarget(ValueError):
    pass


def validate_target(kind: str) -> str:
    normalized = kind.strip().lower()
    if not normalized:
        raise ForbiddenTarget("a suggestion needs a target kind")
    if normalized in FORBIDDEN_TARGET_KINDS or normalized.startswith("gate"):
        raise ForbiddenTarget(f"an AI suggestion cannot target {normalized!r}: gates are decided by a person")
    return normalized


def next_state(state: str, kind: str) -> str:
    try:
        return TRANSITIONS[state][kind]
    except KeyError:
        raise InvalidTransition(f"a {state} suggestion cannot be {kind}") from None


def state_of(kinds: Iterable[str]) -> str:
    state = PENDING_STATE
    for kind in kinds:
        state = next_state(state, kind)
    return state


@dataclass(frozen=True)
class Event:
    kind: str
    final_text: str | None = None


def accepted_text(ai_content: str, events: Sequence[Event]) -> str | None:
    """The text a person made their own: the AI text if accepted as is, their edit if edited first.
    None unless the suggestion is currently accepted."""
    if state_of(e.kind for e in events) != ACCEPTED_STATE:
        return None
    last = next(e for e in reversed(events) if e.kind in (ACCEPTED, EDITED_AND_ACCEPTED))
    return ai_content if last.kind == ACCEPTED else last.final_text


def validate_final_text(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        raise ValueError("the edited text is empty")
    if len(stripped) > MAX_FINAL_TEXT_CHARS:
        raise ValueError(f"the edited text is longer than {MAX_FINAL_TEXT_CHARS} characters")
    return stripped


def validate_confidence(value: float | None) -> float | None:
    if value is not None and not 0.0 <= value <= 1.0:
        raise ValueError("confidence must be between 0 and 1")
    return value


@dataclass(frozen=True)
class BulkPlan:
    accept: list[str]
    discard: list[str]
    skipped: list[str]  # not pending, or no confidence to judge by, or between the two thresholds


def plan_bulk(
    items: Iterable[tuple[str, str, float | None]],
    accept_at_or_above: float | None,
    discard_below: float | None,
) -> BulkPlan:
    """Decide which pending suggestions a bulk action touches. `items` is (id, state, confidence).
    A suggestion without a confidence is never decided in bulk."""
    if accept_at_or_above is None and discard_below is None:
        raise ValueError("give accept_at_or_above, discard_below, or both")
    for threshold in (accept_at_or_above, discard_below):
        validate_confidence(threshold)
    if accept_at_or_above is not None and discard_below is not None and discard_below > accept_at_or_above:
        raise ValueError("discard_below must not be higher than accept_at_or_above")
    accept: list[str] = []
    discard: list[str] = []
    skipped: list[str] = []
    for item_id, state, confidence in items:
        if state != PENDING_STATE or confidence is None:
            skipped.append(item_id)
        elif accept_at_or_above is not None and confidence >= accept_at_or_above:
            accept.append(item_id)
        elif discard_below is not None and confidence < discard_below:
            discard.append(item_id)
        else:
            skipped.append(item_id)
    return BulkPlan(accept, discard, skipped)
