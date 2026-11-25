"""Research figures (plan M8.3): list and read figures, record a human edit as a new version, and preview a version.

Every route is behind `RESEARCH_FIGURES_ENABLED` (404 when off). A figure's versions are append-only (the database
refuses UPDATE and DELETE on `figure_versions`); an edit writes version N+1 with origin `human_edited`.

The preview renders the stored spec with Graphviz (`GRAPHVIZ_DOT_PATH`). If `dot` is missing or fails, the route
answers 503 with a `code`; it never returns a placeholder image.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any, Literal
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, figure_checks, figure_spec, figure_store, figure_suggestions  # noqa: F401  (registers the figure suggestion handler)
from app.ai_controls import AIOffError, ensure_ai_on
from app.config import get_settings
from app.database import get_db
from app.dependencies import READ_ROLES, WRITE_ROLES, current_user, project_access
from app.figure_render import RenderFailed, RendererUnavailable, render_svg, to_dot
from app.fulltext import FullTextError
from app.models import AiSuggestion, Figure, FigureCheck, FigureVersion, Project, UploadedDocument, User
from app.task_handlers import FIGURE_ACCURACY_CHECK, FIGURE_CORRECT, FIGURE_GENERATE
from app.uploaded_documents import error_code
from app.task_queue import enqueue_task

router = APIRouter(prefix="/projects/{project_id}", tags=["figures"])


class FigureVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    figure_id: uuid.UUID
    number: int
    spec_json: dict[str, Any]
    spec_hash: str
    origin: str
    suggestion_id: uuid.UUID | None
    model_id: str | None
    prompt_version: str | None
    created_by: str
    created_at: datetime


class FigureRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    uploaded_document_id: uuid.UUID
    kind: str
    title: str
    created_by: str
    created_at: datetime
    updated_at: datetime


class FigureDetail(FigureRead):
    versions: list[FigureVersionRead]


class FigureVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec: dict[str, Any]


class FigureCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uploaded_document_id: uuid.UUID
    kind: Literal["conceptual_framework", "methodology_flowchart"]


class FigureQueued(BaseModel):
    figure_id: uuid.UUID
    task_id: uuid.UUID
    status: str


def _require_enabled() -> None:
    if not get_settings().research_figures_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Research figures are not enabled")


def _load(db: Session, project_id: uuid.UUID, figure_id: uuid.UUID) -> Figure:
    figure = figure_store.get_figure(db, project_id, figure_id)
    if figure is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Figure not found")
    return figure


def _version(db: Session, figure: Figure, number: int) -> FigureVersion:
    version = figure_store.version_by_number(db, figure, number)
    if version is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Figure version not found")
    return version


@router.get("/figures", response_model=list[FigureRead])
def list_figures(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    _require_enabled()
    return figure_store.list_figures(db, project.id)


@router.get("/figures/{figure_id}", response_model=FigureDetail)
def get_figure(figure_id: uuid.UUID, project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)):
    _require_enabled()
    figure = _load(db, project.id, figure_id)
    return FigureDetail(
        **FigureRead.model_validate(figure).model_dump(),
        versions=[FigureVersionRead.model_validate(v) for v in figure_store.list_versions(db, figure)],
    )


@router.post("/figures", response_model=FigureQueued, status_code=status.HTTP_202_ACCEPTED)
def request_figure(
    body: FigureCreate,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Queue a draft conceptual framework from an uploaded paper (plan M8.4). The draft becomes a pending suggestion
    on the new figure; no version is written until a person accepts it. 409 when AI is off for the project."""
    _require_enabled()
    try:
        ensure_ai_on(project)
    except AIOffError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": "ai_off", "message": str(exc)}) from exc
    document = db.get(UploadedDocument, body.uploaded_document_id)
    if document is None or document.project_id != project.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="That uploaded document is not in this project")
    if document.withdrawn_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="That uploaded document has been withdrawn")
    actor = audit.user_actor(user)
    figure = Figure(
        id=uuid.uuid4(), project_id=project.id, uploaded_document_id=document.id, kind=body.kind,
        title=f"Conceptual framework: {document.filename}"[:200], created_by=actor,
    )
    db.add(figure)
    db.flush()
    task = enqueue_task(
        db, project.id, FIGURE_GENERATE,
        payload={"project_id": str(project.id), "figure_id": str(figure.id), "actor": actor},
        actor=actor,
    )
    audit.record(
        db, actor=actor, action="ai.requested", project_id=project.id,
        payload={"task_id": str(task.id), "action": "generate", "target_kind": "figure", "target_id": str(figure.id)},
    )
    db.commit()
    return FigureQueued(figure_id=figure.id, task_id=task.id, status=task.status.value)


@router.post("/figures/{figure_id}/versions", response_model=FigureVersionRead, status_code=status.HTTP_201_CREATED)
def edit_figure(
    figure_id: uuid.UUID,
    body: FigureVersionCreate,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Record a person's edit of the figure as version N+1. The spec is validated for the figure's kind (422 with
    the first problem found); nothing is written when it is invalid."""
    _require_enabled()
    figure = _load(db, project.id, figure_id)
    actor = audit.user_actor(user)
    try:
        version = figure_store.write_version(
            db, figure, body.spec, origin=figure_store.ORIGIN_HUMAN_EDITED, created_by=actor
        )
    except figure_spec.SpecError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    previous = figure_store.version_by_number(db, figure, version.number - 1) if version.number > 1 else None
    changed = figure_spec.diff(previous.spec_json if previous else {"nodes": [], "edges": []}, version.spec_json)
    audit.record(
        db, actor=actor, action="figure.version_created", project_id=project.id,
        payload={
            "figure_id": str(figure.id), "number": version.number, "spec_hash": version.spec_hash, "origin": version.origin,
            "changes": {key: len(value) for key, value in changed.items()},
        },
    )
    db.commit()
    db.refresh(version)
    return version


def _render_version(figure: Figure, version: FigureVersion) -> bytes:
    """Draw one version with Graphviz. 422 if the stored spec cannot be drawn faithfully; 503 with a `code` if Graphviz
    is missing (`figure_renderer_unavailable`) or fails or emits unsafe output (`figure_render_failed`)."""
    settings = get_settings()
    try:
        dot = to_dot(figure.kind, version.spec_json)
        return render_svg(dot, dot_path=settings.graphviz_dot_path, timeout_seconds=settings.figure_render_timeout_seconds)
    except figure_spec.SpecError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "figure_spec_invalid", "message": str(exc)},
        ) from exc
    except RendererUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "figure_renderer_unavailable", "message": str(exc)},
        ) from exc
    except RenderFailed as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "figure_render_failed", "message": str(exc)},
        ) from exc


@router.get("/figures/{figure_id}/versions/{number}/preview.svg")
def preview_figure_version(
    figure_id: uuid.UUID,
    number: int,
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
):
    """Render one version to SVG with Graphviz. 422 if the stored spec cannot be drawn faithfully; 503 with a
    `code` if Graphviz is missing (`figure_renderer_unavailable`) or fails or emits unsafe output (`figure_render_failed`)."""
    _require_enabled()
    figure = _load(db, project.id, figure_id)
    version = _version(db, figure, number)
    return Response(content=_render_version(figure, version), media_type="image/svg+xml")


def _with_metadata(svg: bytes, fields: dict[str, str]) -> bytes:
    """Insert a `<metadata>` element as the first child of the root `<svg>`. Graphviz writes the root start tag with no
    `>` inside its attributes, so the first `>` after `<svg` closes it."""
    start = svg.index(b"<svg")
    end = svg.index(b">", start) + 1
    children = "".join(f"<{name}>{escape(value)}</{name}>" for name, value in fields.items())
    return svg[:end] + f"<metadata>{children}</metadata>".encode("utf-8") + svg[end:]


@router.get("/figures/{figure_id}/versions/{number}/export.svg")
def export_figure_version(
    figure_id: uuid.UUID,
    number: int,
    project: Project = Depends(project_access(READ_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Export one version as a downloadable SVG with embedded provenance (plan M8.8). A version with deterministic
    `error` findings is refused: 409 `figure_has_errors` lists them. 409 when the source document was withdrawn; 422
    with a `code` when its text cannot be read; 422 and 503 as for the preview."""
    _require_enabled()
    figure = _load(db, project.id, figure_id)
    version = _version(db, figure, number)
    document = db.get(UploadedDocument, figure.uploaded_document_id)
    if document is None or document.withdrawn_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="That uploaded document has been withdrawn")
    try:
        full = figure_store.document_full_text(get_settings(), document)
    except FullTextError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": error_code(exc), "message": str(exc)},
        ) from exc
    errors = [
        f.as_dict() for f in figure_checks.check_figure(version.spec_json, full, figure.kind) if f.severity == figure_checks.ERROR
    ]
    if errors:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": "figure_has_errors", "findings": errors})
    svg = _with_metadata(
        _render_version(figure, version),
        {
            "figure_id": str(figure.id),
            "version": str(version.number),
            "origin": version.origin,
            "model_id": version.model_id or "",
            "prompt_version": version.prompt_version or "",
            "document_sha256": document.sha256,
            "exported_at": datetime.now(UTC).isoformat(),
        },
    )
    audit.record(
        db, actor=audit.user_actor(user), action="figure.exported", project_id=project.id,
        payload={"figure_id": str(figure.id), "number": version.number, "spec_hash": version.spec_hash},
    )
    db.commit()
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={"Content-Disposition": f'attachment; filename="figure-{figure.id}-v{version.number}.svg"'},
    )


class FigureCheckRead(BaseModel):
    id: uuid.UUID
    status: str
    reason: str | None
    labels: dict[str, str]
    model_id: str | None
    prompt_version: str | None
    created_by: str
    created_at: datetime


class FigureCheckDetail(BaseModel):
    deterministic: list[dict[str, Any]]
    ai: FigureCheckRead | None


def _check_read(check: FigureCheck) -> FigureCheckRead:
    return FigureCheckRead(
        id=check.id, status=check.status, reason=check.reason, labels=dict(check.labels_json or {}),
        model_id=check.model_id, prompt_version=check.prompt_version, created_by=check.created_by,
        created_at=check.created_at,
    )


@router.post("/figures/{figure_id}/versions/{number}/check", response_model=FigureQueued, status_code=status.HTTP_202_ACCEPTED)
def request_figure_check(
    figure_id: uuid.UUID,
    number: int,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Queue an AI labelling of one figure version against its paper (plan M8.6). The check is stored for a person to
    read; the figure is not changed. 409 when AI is off for the project."""
    _require_enabled()
    try:
        ensure_ai_on(project)
    except AIOffError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": "ai_off", "message": str(exc)}) from exc
    figure = _load(db, project.id, figure_id)
    version = _version(db, figure, number)
    actor = audit.user_actor(user)
    task = enqueue_task(
        db, project.id, FIGURE_ACCURACY_CHECK,
        payload={"project_id": str(project.id), "figure_id": str(figure.id), "number": version.number, "actor": actor},
        actor=actor,
    )
    audit.record(
        db, actor=actor, action="ai.requested", project_id=project.id,
        payload={"task_id": str(task.id), "action": "check", "target_kind": "figure", "target_id": str(figure.id)},
    )
    db.commit()
    return FigureQueued(figure_id=figure.id, task_id=task.id, status=task.status.value)


@router.post("/figures/{figure_id}/versions/{number}/correct", response_model=FigureQueued, status_code=status.HTTP_202_ACCEPTED)
def request_figure_correction(
    figure_id: uuid.UUID,
    number: int,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Queue an AI correction of one figure version whose deterministic checks fail (plan M8.7). The corrected spec is
    stored as a pending suggestion on the figure only if it passes the checks; nothing is accepted automatically. 409 when AI is off."""
    _require_enabled()
    try:
        ensure_ai_on(project)
    except AIOffError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": "ai_off", "message": str(exc)}) from exc
    figure = _load(db, project.id, figure_id)
    version = _version(db, figure, number)
    actor = audit.user_actor(user)
    task = enqueue_task(
        db, project.id, FIGURE_CORRECT,
        payload={"project_id": str(project.id), "figure_id": str(figure.id), "number": version.number, "actor": actor},
        actor=actor,
    )
    audit.record(
        db, actor=actor, action="ai.requested", project_id=project.id,
        payload={"task_id": str(task.id), "action": "correct", "target_kind": "figure", "target_id": str(figure.id)},
    )
    db.commit()
    return FigureQueued(figure_id=figure.id, task_id=task.id, status=task.status.value)


@router.get("/figures/{figure_id}/versions/{number}/check", response_model=FigureCheckDetail)
def get_figure_check(
    figure_id: uuid.UUID,
    number: int,
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
):
    """The deterministic findings for one version, computed now against the stored spec and the paper's text (plan M8.6),
    and the latest AI check, if any. 409 when the source document was withdrawn; 422 with a `code` when its text cannot be read."""
    _require_enabled()
    figure = _load(db, project.id, figure_id)
    version = _version(db, figure, number)
    document = db.get(UploadedDocument, figure.uploaded_document_id)
    if document is None or document.withdrawn_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="That uploaded document has been withdrawn")
    try:
        full = figure_store.document_full_text(get_settings(), document)
    except FullTextError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": error_code(exc), "message": str(exc)},
        ) from exc
    findings = figure_checks.check_figure(version.spec_json, full, figure.kind)
    latest = db.scalars(
        select(FigureCheck)
        .where(FigureCheck.figure_version_id == version.id)
        .order_by(FigureCheck.created_at.desc())
        .limit(1)
    ).first()
    return FigureCheckDetail(deterministic=[f.as_dict() for f in findings], ai=_check_read(latest) if latest else None)


class FigureSuggestionRead(BaseModel):
    id: uuid.UUID
    action: str | None
    model_id: str | None
    prompt_version: str | None
    base_number: int | None
    proposed: dict[str, Any]
    diff: dict[str, list[str]]


@router.get("/figures/{figure_id}/suggestions/{figure_suggestion_id}", response_model=FigureSuggestionRead)
def get_figure_suggestion(
    figure_id: uuid.UUID,
    figure_suggestion_id: uuid.UUID,
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
):
    """A pending figure suggestion's proposed spec and its diff from the version it was made from (plan M8.7). The base
    is the version whose spec hash the suggestion records; a generated draft has no base and diffs from an empty figure."""
    _require_enabled()
    figure = _load(db, project.id, figure_id)
    suggestion = db.get(AiSuggestion, figure_suggestion_id)
    if (
        suggestion is None
        or suggestion.project_id != project.id
        or suggestion.target_kind != figure_suggestions.TARGET_KIND
        or suggestion.target_id != str(figure.id)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Figure suggestion not found")
    base: dict[str, Any] = {"nodes": [], "edges": []}
    base_number: int | None = None
    if suggestion.input_hash:
        for candidate in figure_store.list_versions(db, figure):
            if candidate.spec_hash == suggestion.input_hash:
                base, base_number = candidate.spec_json, candidate.number
                break
    proposed = figure_spec.normalize(json.loads(suggestion.content), figure.kind)
    return FigureSuggestionRead(
        id=suggestion.id, action=suggestion.action, model_id=suggestion.model_id,
        prompt_version=suggestion.prompt_version, base_number=base_number,
        proposed=proposed, diff=figure_spec.diff(base, proposed),
    )
