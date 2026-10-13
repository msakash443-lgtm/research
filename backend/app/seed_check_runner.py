"""Run the seed-based false-exclusion check (plan M2.9.1): ask the AI pre-screen about each seed paper that
matches a stored source, and store what it said as one `SeedCheckRun`.

It reuses `prescreen.prescreen_source` (same prompt, same confidence rule, same answer checks) but never
writes a `ScreeningDecision`, so nothing here appears in the queue, PRISMA or the AI-human agreement
figures. Fails loudly: a missing LLM configuration stops everything and stores nothing; an answer that
can't be used is recorded on that seed and makes the run `incomplete`, never a guess.
"""

from __future__ import annotations

import uuid
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import ai_quality, audit, seed_check
from app.agent.llm import LLMResponseError, OpenAICompatibleLLM
from app.models import Project, ScreeningCriterion, SeedCheckRun, SeedPaper
from app.prescreen import PrescreenError, load_prescreen_prompt, prescreen_source
from app.screening import autoexclude_threshold, screenable_sources
from app.ai_item_locks import locked_ids


def run_seed_check(
    db: Session,
    project: Project,
    *,
    llm: OpenAICompatibleLLM,
    min_confidence: float,
    autoexclude_default_threshold: float,
    actor: str,
    run_id: uuid.UUID,
    ensure_owned: Callable[[Session], None] | None = None,
) -> SeedCheckRun:
    criteria = list(
        db.scalars(select(ScreeningCriterion).where(ScreeningCriterion.project_id == project.id).order_by(ScreeningCriterion.position))
    )
    if not any(c.kind == "include" for c in criteria) or not any(c.kind == "exclude" for c in criteria):
        raise PrescreenError("Add at least one inclusion and one exclusion criterion before checking the seed papers.")
    prompt = load_prescreen_prompt()
    seeds = db.scalars(
        select(SeedPaper).where(SeedPaper.project_id == project.id).order_by(SeedPaper.created_at, SeedPaper.id)
    ).all()
    threshold = autoexclude_threshold(project, autoexclude_default_threshold)
    locked = locked_ids(db, project.id, "source")  # "never for this item": a locked source is never sent to the model

    entries: list[dict] = []
    for match in seed_check.match_seeds(seeds, screenable_sources(db, project)):
        entry = {"seed_id": str(match.seed.id), "title": match.seed.title}
        if match.status is not None:
            entries.append({**entry, "status": match.status})
            continue
        entry.update(source_id=str(match.source.id), matched_by=match.matched_by)
        if str(match.source.id) in locked:
            entries.append({**entry, "status": seed_check.AI_LOCKED})
            continue
        if ensure_owned:
            ensure_owned(db)
        try:
            suggestion = prescreen_source(llm, criteria, match.source, min_confidence=min_confidence, prompt=prompt)
        except (PrescreenError, LLMResponseError) as exc:
            entries.append({**entry, "status": seed_check.FAILED, "reason": str(exc)[:300]})
            continue
        entries.append({
            **entry,
            "status": seed_check.ANSWERED,
            "decision": suggestion.decision,
            "reason_code": suggestion.reason_code,
            "confidence": suggestion.confidence,
            "rationale": suggestion.rationale,
            "auto_exclude_eligible": ai_quality.is_autoexclude_eligible(suggestion.decision, suggestion.confidence, threshold),
            "flags": list(suggestion.flags),
        })

    summary = seed_check.summarise(entries)
    run = SeedCheckRun(
        id=run_id, project_id=project.id, created_by=actor, model_id=llm._model, prompt_version=prompt.ref,
        min_confidence=min_confidence, autoexclude_threshold=threshold, summary=summary, entries=entries,
    )
    db.add(run)
    audit.record(
        db, actor=actor, action="screening.seed_check_run", project_id=project.id,
        payload={"run_id": str(run_id), "status": summary["status"], "checked": summary["checked"],
                 "excluded": summary["excluded"], "failed": summary["failed"]},
        model_id=llm._model, prompt_version=prompt.ref,
    )
    db.commit()
    return run


__all__ = ["run_seed_check"]
