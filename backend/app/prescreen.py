"""AI pre-screen of one source (spec 5.3.1): a SUGGESTION to a person, never a decision.

The model sees the project's criteria and one record (title and abstract, cleaned and fenced like every
other piece of third-party text, M1.2). It must return JSON; a reply that doesn't validate fails the
step (plan rule 22), it is never repaired. Two rules are applied to a valid reply:

* the reason code must be one of the project's own criteria and match the decision (an exclusion cites
  an exclusion criterion); a reply that breaks this is rejected, not corrected;
* a confidence below `prescreen_min_confidence` becomes "maybe" (M2.2.3): a hesitant model never
  suggests excluding. The original answer is kept in the rationale so nothing is hidden.

The result is stored as a `ScreeningDecision` with `decided_by="ai"` (M2.2.2); this module itself
never writes a human one and never marks a source decided. Whether such a row later counts as the
*effective* decision (M2.5.1 auto-exclude, spec 5.3 human gate) is a downstream policy
(`app.screening.effective_ai_exclusion`) this module only feeds: a high-confidence exclude is
sampled into `AutoExcludeAudit` for a person to confirm/overturn, at the configured rate. Each batch
also checks AI-human agreement (M2.5.2) and auto-disables further pre-screening if it has dropped
too low.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import ai_quality, audit
from app.ai_item_locks import AiItemLocked, is_locked, locked_ids
from app.agent.llm import LLMResponseError, OpenAICompatibleLLM
from app.answer_guard import check_answer
from app.models import AutoExcludeAudit, Project, ScreeningCriterion, ScreeningDecision, Source
from app.screening_suggestions import store_prescreen_suggestion
from app.prompt_registry import Prompt, load_prompt
from app.screening import ai_human_agreement, ai_suggestion, autoexclude_threshold, check_reason, final_decision, next_seq, rows_for_stage, screenable_sources
from app.untrusted_text import clean_untrusted

PRESCREEN_PROMPT = ("screening_prescreen", 2)
AI_DECIDER = "agent:prescreen"
MAX_TITLE_CHARS = 300
MAX_ABSTRACT_CHARS = 4000
MAX_RATIONALE_CHARS = 600
MAX_QUOTE_CHARS = 300
HIGH_CONFIDENCE = 0.8
MEDIUM_CONFIDENCE = 0.5

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["decision", "reason_code", "confidence", "rationale", "evidence_quote"],
    "properties": {
        "decision": {"enum": ["include", "exclude", "maybe"]},
        "reason_code": {"type": ["string", "null"], "maxLength": 10},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "rationale": {"type": "string", "minLength": 1, "maxLength": MAX_RATIONALE_CHARS},
        "evidence_quote": {"type": ["string", "null"], "maxLength": MAX_QUOTE_CHARS},
    },
}


class PrescreenError(RuntimeError):
    """The model's answer can't be used. The message says why; nothing is stored for the source."""


@dataclass(frozen=True)
class Suggestion:
    decision: str
    reason_code: str | None
    confidence: float
    rationale: str
    model_id: str | None
    prompt_ref: str
    flags: tuple[str, ...]  # instruction-like text etc. found in the record
    evidence_quote: str | None = None  # words of the record that decided it, checked to be really there (X.77)
    evidence_source: str | None = None  # "title" or "abstract": where that quote was found


def load_prescreen_prompt() -> Prompt:
    return load_prompt(*PRESCREEN_PROMPT)


def _criteria_text(criteria: list[ScreeningCriterion]) -> str:
    lines = []
    for kind, heading in (("include", "Inclusion criteria"), ("exclude", "Exclusion criteria")):
        lines.append(f"{heading}:")
        lines.extend(f"- {c.code}: {c.text}" for c in criteria if c.kind == kind)
    return "\n".join(lines)


def confidence_level(confidence: float | None) -> str | None:
    """A plain label for a confidence number, shown next to a suggestion (X.77): high, medium or low."""
    if confidence is None:
        return None
    return "high" if confidence >= HIGH_CONFIDENCE else "medium" if confidence >= MEDIUM_CONFIDENCE else "low"


def locate_quote(quote: str, title: str, abstract: str | None) -> tuple[str, str] | None:
    """Where `quote` appears word for word in the record, as (source, the record's own spelling), or None.

    Whitespace and letter case are ignored (a model often capitalises the start of a sentence); the words are not."""
    wanted = " ".join(quote.split()).casefold()
    if not wanted:
        return None
    for source, text in (("title", title), ("abstract", abstract or "")):
        flat = " ".join(text.split())
        at = flat.casefold().find(wanted)
        if at >= 0:
            return source, flat[at : at + len(wanted)]
    return None


def build_prompts(prompt: Prompt, criteria: list[ScreeningCriterion], source: Source, nonce: str) -> tuple[str, str, tuple[str, ...]]:
    title = clean_untrusted(source.title, MAX_TITLE_CHARS)
    abstract = clean_untrusted(source.abstract, MAX_ABSTRACT_CHARS) if source.abstract else None
    flags = tuple(sorted({*title.flags, *(abstract.flags if abstract else ())}))
    record = prompt.render_source(
        index="1",
        nonce=nonce,
        title=title.text,
        status="no abstract available" if abstract is None else "title and abstract",
        details=str(source.year) if source.year else "year not recorded",
        excerpt=abstract.text if abstract else "(No abstract is available. Judge from the title only; if that is not enough, answer maybe.)",
    )
    return prompt.system, prompt.render_user(criteria=_criteria_text(criteria), record=record), flags


def prescreen_source(
    llm: OpenAICompatibleLLM, criteria: list[ScreeningCriterion], source: Source, *, min_confidence: float, prompt: Prompt | None = None
) -> Suggestion:
    """Ask the model for a suggestion. Raises `PrescreenError` or an `LLM*Error`; never returns a placeholder."""
    prompt = prompt or load_prescreen_prompt()
    nonce = secrets.token_hex(8)
    system_prompt, user_prompt, flags = build_prompts(prompt, criteria, source, nonce)
    reply = llm.complete_json(system_prompt, user_prompt, SCHEMA)
    quote_text = reply.get("evidence_quote")
    leaked = check_answer(str(reply.get("rationale", "")), system_prompt=system_prompt, nonce=nonce)
    if "<<<" in reply["rationale"]:
        leaked = leaked or "the record markers"
    if leaked:
        raise PrescreenError(f"The model's answer repeated {leaked}, so it was rejected.")

    decision = reply["decision"]
    reason = (reply["reason_code"] or "").strip() or None
    problem = check_reason(decision, reason, {c.code: c.kind for c in criteria})
    if problem:
        raise PrescreenError(f"The model's answer was rejected: {problem}")
    evidence_quote = evidence_source = None
    if quote_text is not None and quote_text.strip():
        title_text = clean_untrusted(source.title, MAX_TITLE_CHARS).text
        abstract_text = clean_untrusted(source.abstract, MAX_ABSTRACT_CHARS).text if source.abstract else None
        found = locate_quote(quote_text, title_text, abstract_text)
        if found is None:
            raise PrescreenError("The model's answer was rejected: its evidence quote is not in the record's title or abstract.")
        evidence_source, evidence_quote = found
    elif decision in ("include", "exclude"):
        raise PrescreenError(f"The model's answer was rejected: it suggested '{decision}' without an evidence quote from the record.")
    rationale = " ".join(reply["rationale"].split())
    confidence = float(reply["confidence"])
    if confidence < min_confidence and decision != "maybe":
        rationale = f"Low confidence ({confidence:.2f}): the model first answered '{decision}'. {rationale}"[:MAX_RATIONALE_CHARS + 120]
        decision, reason = "maybe", None  # a hesitant model never suggests excluding or including
    return Suggestion(decision, reason, confidence, rationale, llm._model, prompt.ref, flags, evidence_quote, evidence_source)


@dataclass
class PrescreenResult:
    suggested: int = 0
    failed: int = 0
    skipped: int = 0  # decided or suggested by someone else while this ran
    locked: int = 0  # left for a person because the source is set to "never for this item"


def run_prescreen(
    db: Session,
    project: Project,
    *,
    llm: OpenAICompatibleLLM,
    min_confidence: float,
    actor: str,
    ensure_owned: Callable[[Session], None] | None = None,
    limit: int = 200,
    autoexclude_default_threshold: float = 0.9,
    autoexclude_sample_rate: float = 0.10,
    agreement_min_n: int = 20,
    agreement_min_kappa: float = 0.4,
) -> PrescreenResult:
    """Suggest a decision for each source a person hasn't decided and the AI hasn't looked at yet (title/abstract).

    One source at a time, committed as it goes, so a stop part-way keeps what was done and a re-run
    carries on. A source whose answer can't be used is recorded in the audit log and left for a person;
    a missing LLM configuration stops everything (`LLMConfigurationError`).

    Refuses outright if `project.ai_prescreen_disabled` (M2.5.2): an owner must look at why before
    pre-screening resumes. If `project.ai_autoexclude_enabled` (M2.5.1), a high-confidence exclude is
    sampled into the human audit at `autoexclude_sample_rate`; after the batch, AI-human agreement is
    checked and pre-screening auto-disables if it has dropped below `agreement_min_kappa`.
    """
    if project.ai_prescreen_disabled:
        raise PrescreenError(project.ai_prescreen_disabled_reason or "AI pre-screen is disabled for this project.")
    criteria = list(
        db.scalars(select(ScreeningCriterion).where(ScreeningCriterion.project_id == project.id).order_by(ScreeningCriterion.position))
    )
    if not any(c.kind == "include" for c in criteria) or not any(c.kind == "exclude" for c in criteria):
        raise PrescreenError("Add at least one inclusion and one exclusion criterion before pre-screening.")
    prompt = load_prescreen_prompt()
    grouped = rows_for_stage(db, project, "title_abstract")
    locked = locked_ids(db, project.id, "source")  # "never for this item": a locked source is never sent to the model
    undecided = [
        s for s in screenable_sources(db, project)
        if final_decision(grouped.get(s.id, [])) is None and ai_suggestion(grouped.get(s.id, [])) is None
    ]
    todo = [s for s in undecided if str(s.id) not in locked]
    threshold = autoexclude_threshold(project, autoexclude_default_threshold)
    result = PrescreenResult(locked=len(undecided) - len(todo))
    for source in todo[:limit]:
        if ensure_owned:
            ensure_owned(db)
        if is_locked(db, project.id, "source", source.id):
            result.locked += 1
            continue
        try:
            suggestion = prescreen_source(llm, criteria, source, min_confidence=min_confidence, prompt=prompt)
            if is_locked(db, project.id, "source", source.id):
                db.rollback()
                result.locked += 1
                continue
        except (PrescreenError, LLMResponseError) as exc:
            result.failed += 1
            audit.record(
                db, actor=audit.SYSTEM_WORKER, action="screening.ai_failed", project_id=project.id,
                payload={"source_id": str(source.id), "reason": str(exc)[:300]}, model_id=llm._model, prompt_version=prompt.ref,
            )
            db.commit()
            continue
        decision_row = ScreeningDecision(
            project_id=project.id, source_id=source.id, stage="title_abstract", seq=next_seq(db, source.id, "title_abstract"),
            decision=suggestion.decision, reason_code=suggestion.reason_code, decided_by="ai", decider=AI_DECIDER,
            confidence=suggestion.confidence, note=suggestion.rationale,
        )
        db.add(decision_row)
        audit.record(
            db, actor=actor, action="screening.ai_suggested", project_id=project.id,
            payload={"source_id": str(source.id), "stage": "title_abstract", "decision": suggestion.decision,
                     "reason_code": suggestion.reason_code, "confidence": suggestion.confidence, "flags": list(suggestion.flags)},
            model_id=suggestion.model_id, prompt_version=suggestion.prompt_ref,
        )
        try:
            # One transaction: the decision, and (if due) its audit row, commit together. A crash between them
            # would otherwise leave an unsampled eligible exclude that counts forever (M2.5.1 review, 2026-10-11).
            db.flush()  # gives decision_row its id; an IntegrityError here means a person decided first
            store_prescreen_suggestion(
                db, project=project, source=source, decision=suggestion.decision, reason_code=suggestion.reason_code,
                confidence=suggestion.confidence, rationale=suggestion.rationale, model_id=suggestion.model_id,
                prompt_version=suggestion.prompt_ref, producing_agent=AI_DECIDER.removeprefix("agent:"),
                evidence_quote=suggestion.evidence_quote, evidence_source=suggestion.evidence_source,
            )
            if project.ai_autoexclude_enabled and ai_quality.is_autoexclude_eligible(suggestion.decision, suggestion.confidence, threshold):
                ordinal = db.scalar(
                    select(func.count()).select_from(ScreeningDecision).where(
                        ScreeningDecision.project_id == project.id, ScreeningDecision.decided_by == "ai",
                        ScreeningDecision.decision == "exclude", ScreeningDecision.confidence >= threshold,
                    )
                ) or 0
                if ai_quality.due_for_sample(ordinal, autoexclude_sample_rate):
                    db.add(AutoExcludeAudit(
                        project_id=project.id, source_id=source.id, stage="title_abstract",
                        decision_id=decision_row.id, confidence=suggestion.confidence,
                    ))
                    audit.record(
                        db, actor=audit.SYSTEM_WORKER, action="screening.autoexclude_sampled", project_id=project.id,
                        payload={"source_id": str(source.id), "confidence": suggestion.confidence},
                    )
            db.commit()
            result.suggested += 1
        except (IntegrityError, AiItemLocked):  # a person or lock changed this source while the model was answering
            db.rollback()
            result.skipped += 1
            continue
    if not project.ai_prescreen_disabled:
        agreement = ai_human_agreement(db, project, "title_abstract")
        if ai_quality.should_disable_prescreen(agreement, min_n=agreement_min_n, min_kappa=agreement_min_kappa):
            project.ai_prescreen_disabled = True
            project.ai_prescreen_disabled_reason = (
                f"AI-human agreement (kappa={agreement.kappa:.2f} over {agreement.n} compared decisions) "
                f"fell below the {agreement_min_kappa} threshold."
            )
            audit.record(
                db, actor=audit.SYSTEM_WORKER, action="screening.ai_prescreen_auto_disabled", project_id=project.id,
                payload={"kappa": agreement.kappa, "n": agreement.n, "threshold": agreement_min_kappa},
            )
            db.commit()
    return result


__all__ = ["PRESCREEN_PROMPT", "AI_DECIDER", "PrescreenError", "PrescreenResult", "Suggestion", "confidence_level", "locate_quote", "prescreen_source", "run_prescreen", "build_prompts"]
