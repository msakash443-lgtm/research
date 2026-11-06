"""Token usage and the per-project budget (M0.9.1).

The meter is given to `OpenAICompatibleLLM`. Before each call it refuses (loudly) if the project has
already used its budget; after each call it records what the provider reported. Rows are written in
their own short transaction so spend is kept even when the surrounding step fails and rolls back.

Limits, stated plainly: the budget is checked *before* a call, against recorded usage plus the reservations of
calls in flight (X.110), so one call can overshoot it but concurrent calls cannot all pass the check; and a
provider that returns no `usage` is recorded with unknown (NULL) tokens, which count as zero here.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.llm import MAX_RETRY_AFTER_SECONDS, TRANSIENT_ATTEMPTS, LLMConfigurationError
from app.config import Settings
from app.database import SessionLocal
from app.models import LlmUsage, Project


class LLMBudgetExceeded(LLMConfigurationError):
    """The project has used its token budget; the call was not made."""


def tokens_used(db: Session, project_id: uuid.UUID) -> int:
    return int(db.scalar(select(func.coalesce(func.sum(LlmUsage.total_tokens), 0)).where(LlmUsage.project_id == project_id)) or 0)


def reservation_window(settings: Settings) -> timedelta:
    """How long a reservation counts before it is treated as dead (X.110). A call still alive finishes within it:
    every attempt may time out and wait out the longest Retry-After, plus a margin."""
    longest_timeout = max(settings.llm_timeout_seconds, settings.local_llm_timeout_seconds)
    return timedelta(seconds=TRANSIENT_ATTEMPTS * (longest_timeout + MAX_RETRY_AFTER_SECONDS) + 60)


def reserved_tokens(db: Session, project_id: uuid.UUID, window: timedelta) -> int:
    """Tokens held for calls still in flight (X.110); they count against the budget at admission. A reservation older
    than `window` belongs to a process that died mid-call and no longer counts, so it cannot block the project for ever."""
    live_after = datetime.now(timezone.utc) - window
    return int(db.scalar(select(func.coalesce(func.sum(LlmUsage.reserved_tokens), 0)).where(
        LlmUsage.project_id == project_id, LlmUsage.reserved_tokens.is_not(None), LlmUsage.created_at >= live_after)) or 0)


NEAR_BUDGET = 0.8  # from this share of the budget on, the status says the stop is close


def budget_status(used: int, budget: int | None) -> dict[str, int | float | str | None]:
    """How close a project is to its token budget (X.79.1): `no_limit`, `ok`, `near` (at least 80% used) or `over`
    (the budget is reached, so `ProjectMeter.before_call` now refuses every call). `remaining` never goes below 0."""
    if not budget:
        return {"status": "no_limit", "remaining": None, "percent_used": None}
    status = "over" if used >= budget else "near" if used >= budget * NEAR_BUDGET else "ok"
    return {"status": status, "remaining": max(budget - used, 0), "percent_used": round(used * 100 / budget, 1)}


def parse_usage(data: object) -> tuple[int | None, int | None, int | None]:
    """(prompt, completion, total) from a chat-completions `usage` object; None where the provider didn't say."""
    usage = data.get("usage") if isinstance(data, dict) else None
    if not isinstance(usage, dict):
        return None, None, None

    def count(key: str) -> int | None:
        value = usage.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None

    prompt, completion, total = count("prompt_tokens"), count("completion_tokens"), count("total_tokens")
    if total is None and prompt is not None and completion is not None:
        total = prompt + completion
    return prompt, completion, total


class ProjectMeter:
    def __init__(
        self, project_id: uuid.UUID, settings: Settings, *, purpose: str, run_id: uuid.UUID | None = None,
        budget_override: int | None = None,
    ):
        self.project_id, self.purpose, self.run_id = project_id, purpose, run_id
        # None = no project-specific override, use the global default; 0 or a positive number is the
        # project's own budget (0 means "no limit" here too, same as the global setting).
        self.budget = settings.project_token_budget if budget_override is None else budget_override
        self._window = reservation_window(settings)

    def before_call(self, max_tokens: int = 0) -> uuid.UUID | None:
        """Admit a call only if recorded usage plus the reservations of calls still in flight is under the budget,
        then hold `max_tokens` for it and return its ticket (X.110). The project row lock makes the check and the
        reservation one step, so concurrent calls cannot all pass. A call admitted under the budget may still use
        more than what is left: the budget is a limit on admission, not a cap on each call."""
        if not self.budget:
            return None
        with SessionLocal() as db:
            db.execute(select(Project.id).where(Project.id == self.project_id).with_for_update())
            used = tokens_used(db, self.project_id) + reserved_tokens(db, self.project_id, self._window)
            if used >= self.budget:
                db.rollback()
                raise LLMBudgetExceeded(
                    f"This project has used its token budget ({used} of {self.budget}). No further model calls "
                    "will be made until the budget is raised."
                )
            row = LlmUsage(
                project_id=self.project_id, run_id=self.run_id, purpose=self.purpose, model="",
                reserved_tokens=max(max_tokens, 0),
            )
            db.add(row)
            db.flush()
            ticket = row.id
            db.commit()
        return ticket

    def after_call(self, model: str, data: object, ticket: uuid.UUID | None = None) -> None:
        """Record what the provider reported and release the call's reservation (the same row, when there is a ticket)."""
        prompt, completion, total = parse_usage(data)
        with SessionLocal() as db:
            row = db.get(LlmUsage, ticket) if ticket is not None else None
            if row is None:
                row = LlmUsage(project_id=self.project_id, run_id=self.run_id, purpose=self.purpose)
                db.add(row)
            row.model, row.prompt_tokens, row.completion_tokens, row.total_tokens = model, prompt, completion, total
            row.reserved_tokens = None
            db.commit()

    def abort_call(self, ticket: uuid.UUID | None) -> None:
        """A call that failed before its usage could be recorded gives its reservation back."""
        if ticket is None:
            return
        with SessionLocal() as db:
            row = db.get(LlmUsage, ticket)
            if row is not None:
                db.delete(row)
                db.commit()
