"""Project-wide AI controls (X.31.7): the one place that decides whether, and through which model, a project's AI calls run.

Every step that sends project text to a model builds its adapter with `llm_for_project`, so "AI off" and
"local model only" cannot be forgotten by one caller. Off means no project text goes to any AI.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status

from app.agent.llm import OpenAICompatibleLLM
from app.config import Settings
from app.llm_usage import ProjectMeter
from app.models import AiInvolvement, AiModelRoute, Project

AI_OFF_MESSAGE = "AI is turned off for this project, so nothing was sent to a model. A project owner can change this in the project's AI settings."
LOCAL_ROUTE_EMBEDDINGS_MESSAGE = (
    "This project is set to use only the local model, and the embedding model is not covered by that setting, "
    "so no text was sent to it. Change the project's model route to use embeddings."
)


class AIOffError(RuntimeError):
    """The project's AI setting is Off: no project text may be sent to a model."""


def ensure_ai_on(project: Project) -> None:
    if project.ai_involvement == AiInvolvement.off:
        raise AIOffError(AI_OFF_MESSAGE)


def ensure_embeddings_allowed(project: Project) -> None:
    """Embedding calls send source text to `EMBEDDING_*`, which has no local-only variant: refuse on Off or a local route."""
    ensure_ai_on(project)
    if project.ai_model_route == AiModelRoute.local:
        raise AIOffError(LOCAL_ROUTE_EMBEDDINGS_MESSAGE)


def require_ai_on(project: Project, *, embeddings: bool = False) -> None:
    """For request handlers: refuse with 409 (the project's setting is the conflict) instead of queueing work that would only fail."""
    try:
        (ensure_embeddings_allowed if embeddings else ensure_ai_on)(project)
    except AIOffError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


def llm_for_project(settings: Settings, project: Project, *, purpose: str, run_id: uuid.UUID | None = None) -> OpenAICompatibleLLM:
    """The model adapter for one of this project's AI steps, metered against the project's token budget.

    Raises `AIOffError` when the project's AI is Off, and `LLMConfigurationError` when the project is routed to
    the local model and none is configured (never a silent fallback to the third-party model).
    """
    ensure_ai_on(project)
    meter = ProjectMeter(project.id, settings, purpose=purpose, run_id=run_id, budget_override=project.token_budget_override)
    if project.ai_model_route == AiModelRoute.local:
        return OpenAICompatibleLLM.local(settings, meter=meter)
    return OpenAICompatibleLLM(settings, meter=meter)
