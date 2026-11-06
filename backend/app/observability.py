"""Per-project observability (X.7): how each background step ended, how long completed steps took, and what the model calls used.

Read-only aggregation over `tasks` and `llm_usage`. Only counts, names and timings are returned: never error text,
prompts, answers or source text (the same rule as the audit log). Cost comes from the hand-kept price table in `app/pricing.py`.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import LlmUsage, Task, TaskStatus
from app.pricing import call_cost_usd

def _count_if(condition):
    return func.coalesce(func.sum(case((condition, 1), else_=0)), 0)


def _window(column, since, until):
    """Conditions keeping `column` in [since, until): `since` inclusive, `until` exclusive; a bound left None is open.

    On SQLite (dev and tests) times compare as text, so a row stamped by the `server_default` (whole seconds, no fraction)
    at exactly `since` falls outside the window. Postgres compares `timestamptz` values and keeps it.
    """
    conditions = []
    if since is not None:
        conditions.append(column >= since)
    if until is not None:
        conditions.append(column < until)
    return conditions


def step_summary(db: Session, project_id: uuid.UUID, since: datetime | None = None, until: datetime | None = None) -> list[dict]:
    """One row per task type, counting the tasks created in the window: how many were enqueued and how many ended in each state."""
    rows = db.execute(
        select(
            Task.type,
            func.count(),
            _count_if(Task.status == TaskStatus.completed),
            _count_if(Task.status == TaskStatus.failed),
            _count_if(Task.status == TaskStatus.queued),
            _count_if(Task.status == TaskStatus.running),
            _count_if(Task.status == TaskStatus.paused),
            _count_if(Task.status == TaskStatus.blocked),
            _count_if(Task.attempts > 1),
        )
        .where(Task.project_id == project_id, *_window(Task.created_at, since, until))
        .group_by(Task.type)
        .order_by(Task.type)
    ).all()
    return [
        {"type": r[0], "total": r[1], "completed": int(r[2]), "failed": int(r[3]), "queued": int(r[4]),
         "running": int(r[5]), "paused": int(r[6]), "blocked": int(r[7]), "retried": int(r[8])}
        for r in rows
    ]


def step_durations(db: Session, project_id: uuid.UUID, since: datetime | None = None, until: datetime | None = None) -> list[dict]:
    """One row per task type: how many completed steps were timed, and the average and longest time of the final attempt.

    A step is timed only if it completed and recorded both a start and a finish (tasks run before X.7.1 have neither).
    """
    types = [r[0] for r in db.execute(select(Task.type).where(Task.project_id == project_id, *_window(Task.created_at, since, until)).distinct()).all()]
    timings: dict[str, list[float]] = defaultdict(list)
    for task_type, started, finished in db.execute(
        select(Task.type, Task.started_at, Task.finished_at).where(
            Task.project_id == project_id, Task.status == TaskStatus.completed, *_window(Task.created_at, since, until),
            Task.started_at.is_not(None), Task.finished_at.is_not(None),
        )
    ).all():
        timings[task_type].append((finished - started).total_seconds())
    rows = []
    for task_type in sorted(types):
        seconds = timings.get(task_type, [])
        rows.append({
            "type": task_type,
            "timed": len(seconds),
            "avg_seconds": round(sum(seconds) / len(seconds), 3) if seconds else None,
            "max_seconds": round(max(seconds), 3) if seconds else None,
        })
    return rows


def recent_failures(db: Session, project_id: uuid.UUID, limit: int = 20, since: datetime | None = None, until: datetime | None = None) -> list[dict]:
    """The newest failed steps: id, type, when the step ended, attempts used and, for research runs, the run id. Never the error text."""
    rows = db.execute(
        select(Task.id, Task.type, Task.finished_at, Task.attempts, Task.payload)
        .where(Task.project_id == project_id, Task.status == TaskStatus.failed, *_window(Task.created_at, since, until))
        .order_by(Task.finished_at.desc().nulls_last(), Task.created_at.desc())
        .limit(limit)
    ).all()
    return [
        {
            "id": str(r[0]), "type": r[1], "finished_at": r[2], "attempts": r[3],
            "run_id": (r[4] or {}).get("run_id") if r[1] == "research_run" else None,
        }
        for r in rows
    ]


def model_call_summary(db: Session, project_id: uuid.UUID, since: datetime | None = None, until: datetime | None = None) -> list[dict]:
    """One row per (purpose, model): calls, tokens over the calls whose provider reported a count, and the priced cost.

    `cost_usd` sums only the priced calls (None when none of the row's calls has a price); `calls_unpriced` counts the rest.
    """
    rows = db.execute(
        select(
            LlmUsage.purpose,
            LlmUsage.model,
            func.count(),
            func.coalesce(func.sum(LlmUsage.total_tokens), 0),
            func.count(LlmUsage.total_tokens),
        )
        .where(LlmUsage.project_id == project_id, LlmUsage.reserved_tokens.is_(None), *_window(LlmUsage.created_at, since, until))  # answered calls; a reservation is not a call (X.110/X.132)
        .group_by(LlmUsage.purpose, LlmUsage.model)
        .order_by(LlmUsage.purpose, LlmUsage.model)
    ).all()
    priced: dict[tuple[str, str], Decimal] = {}
    unpriced: dict[tuple[str, str], int] = defaultdict(int)
    for purpose, model, prompt, completion in db.execute(
        select(LlmUsage.purpose, LlmUsage.model, LlmUsage.prompt_tokens, LlmUsage.completion_tokens).where(
            LlmUsage.project_id == project_id, LlmUsage.reserved_tokens.is_(None), *_window(LlmUsage.created_at, since, until),
        )
    ).all():
        cost = call_cost_usd(model, prompt, completion)
        key = (purpose, model)
        if cost is None:
            unpriced[key] += 1
        else:
            priced[key] = priced.get(key, Decimal(0)) + cost
    out = []
    for r in rows:
        key = (r[0], r[1])
        out.append({
            "purpose": r[0], "model": r[1], "calls": r[2], "tokens": int(r[3]),
            "calls_without_token_counts": r[2] - r[4],
            "cost_usd": round(float(priced[key]), 6) if key in priced else None,
            "calls_unpriced": unpriced.get(key, 0),
        })
    return out


def project_observability(db: Session, project_id: uuid.UUID, since: datetime | None = None, until: datetime | None = None) -> dict:
    steps = step_summary(db, project_id, since, until)
    durations = step_durations(db, project_id, since, until)
    calls = model_call_summary(db, project_id, since, until)
    return {
        "steps": steps,
        "step_durations": durations,
        "steps_total": sum(s["total"] for s in steps),
        "steps_failed": sum(s["failed"] for s in steps),
        "steps_retried": sum(s["retried"] for s in steps),
        "model_calls": calls,
        "model_calls_total": sum(c["calls"] for c in calls),
        "tokens_total": sum(c["tokens"] for c in calls),
        "cost_usd_total": round(sum(c["cost_usd"] for c in calls if c["cost_usd"] is not None), 6),
        "calls_unpriced_total": sum(c["calls_unpriced"] for c in calls),
        "failures": recent_failures(db, project_id, since=since, until=until),
    }
