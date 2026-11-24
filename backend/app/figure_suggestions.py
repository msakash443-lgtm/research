"""Figure suggestions (plan M8.3): accepting an AI-proposed figure writes a new version of it.

Registered on import, like `extraction_suggestions.py`. `app/task_handlers.py` and `app/routers/figures.py` import
this module, so the web and worker processes both have the handler.

Undo never deletes history: it writes a new version (origin `human_edited`) holding the spec of the version before
the one the suggestion wrote. An undo of the first version has nothing to restore and is refused.
"""

from __future__ import annotations

import json
import uuid

from sqlalchemy.orm import Session

from app import figure_spec, figure_store, suggestion_store
from app.models import AiSuggestion, Figure, FigureVersion

TARGET_KIND = "figure"


def _figure(db: Session, suggestion: AiSuggestion) -> Figure:
    figure = db.get(Figure, uuid.UUID(suggestion.target_id))
    if figure is None or figure.project_id != suggestion.project_id:
        raise ValueError("The figure this suggestion is about no longer exists.")
    return figure


def _apply(db: Session, suggestion: AiSuggestion, text: str, actor: str) -> str:
    figure = _figure(db, suggestion)
    try:
        spec = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("The suggested figure is not valid JSON.") from exc
    try:
        version = figure_store.write_version(
            db, figure, spec, origin=figure_store.ORIGIN_AI_ACCEPTED, created_by=actor, suggestion=suggestion
        )
    except figure_spec.SpecError as exc:
        raise ValueError(str(exc)) from exc
    return str(version.id)


def _revert(db: Session, suggestion: AiSuggestion, result_ref: str | None, actor: str) -> str:
    figure = _figure(db, suggestion)
    written = db.get(FigureVersion, uuid.UUID(result_ref)) if result_ref else None
    previous = figure_store.version_by_number(db, figure, written.number - 1) if written and written.number > 1 else None
    if previous is None:
        raise ValueError("Nothing earlier to restore: this figure has no earlier version to undo to.")
    version = figure_store.write_version(
        db, figure, previous.spec_json, origin=figure_store.ORIGIN_HUMAN_EDITED, created_by=actor
    )
    return str(version.id)


suggestion_store.TARGET_HANDLERS[TARGET_KIND] = suggestion_store.TargetHandler(_apply, _revert)
