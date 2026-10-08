"""Token usage and the per-project budget (M0.9.1).

The meter is given to `OpenAICompatibleLLM`. Before each call it refuses (loudly) if the project has
already used its budget; after each call it records what the provider reported. Rows are written in
their own short transaction so spend is kept even when the surrounding step fails and rolls back.

Limits, stated plainly: the budget is checked *before* a call, so one call can overshoot it; and a
provider that returns no `usage` is recorded with unknown (NULL) tokens, which count as zero here.
"""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.llm import LLMConfigurationError
from app.config import Settings
from app.database import SessionLocal
from app.models import LlmUsage


class LLMBudgetExceeded(LLMConfigurationError):
    """The project has used its token budget; the call was not made."""


def tokens_used(db: Session, project_id: uuid.UUID) -> int:
    return int(db.scalar(select(func.coalesce(func.sum(LlmUsage.total_tokens), 0)).where(LlmUsage.project_id == project_id)) or 0)


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
    def __init__(self, project_id: uuid.UUID, settings: Settings, *, purpose: str, run_id: uuid.UUID | None = None):
        self.project_id, self.purpose, self.run_id = project_id, purpose, run_id
        self.budget = settings.project_token_budget

    def before_call(self) -> None:
        if not self.budget:
            return
        with SessionLocal() as db:
            used = tokens_used(db, self.project_id)
        if used >= self.budget:
            raise LLMBudgetExceeded(
                f"This project has used its token budget ({used} of {self.budget}). No further model calls "
                "will be made until the budget is raised."
            )

    def after_call(self, model: str, data: object) -> None:
        prompt, completion, total = parse_usage(data)
        with SessionLocal() as db:
            db.add(LlmUsage(
                project_id=self.project_id, run_id=self.run_id, purpose=self.purpose, model=model,
                prompt_tokens=prompt, completion_tokens=completion, total_tokens=total,
            ))
            db.commit()
