"""Dataset versions (M5.1, spec 5.8.3). Raw data is stored exactly as uploaded; there is no route that edits or deletes it.

Cleaning creates a new version that points at its parent. A dataset whose stored text no longer matches its hash is
reported as not intact and cannot be cleaned (fail loudly, plan rule 22).

Case exclusions (M5.7.1) are proposed, then approved by a person with approve rights, and only approved rules can drop
rows. The codebook (M5.7.2) is generated on read and stored nowhere.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, codebook, datasets, exclusion_rules, response_quality, survey_google_forms
from app.config import get_settings
from app.database import get_db
from app.dependencies import APPROVE_ROLES, READ_ROLES, WRITE_ROLES, current_user, project_access
from app.models import DatasetVersion, ExclusionRule, Project, User, utcnow

router = APIRouter(prefix="/projects/{project_id}/datasets", tags=["datasets"])

MAX_CHARS = 5_000_000


class DatasetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    csv_text: str = Field(min_length=1, max_length=MAX_CHARS)


class CleaningStepIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["trim_whitespace", "drop_blank_rows", "drop_columns", "pseudonymise_columns"]
    columns: list[str] | None = Field(default=None, max_length=200)


class CleanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    steps: list[CleaningStepIn] = Field(min_length=1, max_length=20)


def _intact(version: DatasetVersion) -> bool:
    return datasets.sha256_hex(version.content) == version.sha256


def _read(version: DatasetVersion) -> dict[str, Any]:
    return {
        "id": str(version.id),
        "version_no": version.version_no,
        "parent_id": str(version.parent_id) if version.parent_id else None,
        "kind": version.kind,
        "name": version.name,
        "sha256": version.sha256,
        "intact": _intact(version),
        "row_count": version.row_count,
        "column_names": version.column_names,
        "cleaning_steps": version.cleaning_steps,
        "created_by": version.created_by,
        "created_at": version.created_at.isoformat() if isinstance(version.created_at, datetime) else None,
    }


def _version(db: Session, project: Project, dataset_id: uuid.UUID) -> DatasetVersion:
    version = db.get(DatasetVersion, dataset_id)
    if version is None or version.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dataset version not found in this project.")
    return version


def _next_version_no(db: Session, project: Project) -> int:
    existing = db.scalars(select(DatasetVersion.version_no).where(DatasetVersion.project_id == project.id)).all()
    return (max(existing) if existing else 0) + 1


def _store_raw(db: Session, project: Project, user: User, name: str, csv_text: str, *, source: str) -> DatasetVersion:
    """Parse, hash and store a raw version. `source` goes in the audit log; secrets never do."""
    try:
        text, columns, rows = datasets.parse(csv_text.encode("utf-8"))
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    version = DatasetVersion(
        project_id=project.id,
        version_no=_next_version_no(db, project),
        parent_id=None,
        kind="raw",
        name=name.strip(),
        content=text,
        sha256=datasets.sha256_hex(text),
        row_count=len(rows),
        column_names=columns,
        cleaning_steps=[],
        created_by=str(user.id),
    )
    db.add(version)
    db.flush()
    audit.record(
        db, actor=audit.user_actor(user), action="dataset.ingested", project_id=project.id,
        payload={"dataset_id": str(version.id), "sha256": version.sha256, "rows": len(rows),
                 "columns": len(columns), "source": source},
    )
    db.commit()
    db.refresh(version)
    return version


@router.post("", status_code=status.HTTP_201_CREATED)
def ingest(
    body: DatasetIn,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return _read(_store_raw(db, project, user, body.name, body.csv_text, source="upload"))


class GoogleFormsImportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    form_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)


class QualityIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attention: dict[str, str] = Field(default_factory=dict, max_length=50)
    min_seconds: float = Field(default=response_quality.DEFAULT_MIN_SECONDS, ge=0, le=3600)
    straightline_min_items: int = Field(default=response_quality.DEFAULT_STRAIGHTLINE_MIN_ITEMS, ge=2, le=200)
    # Columns that are grid items (straight-lining runs only over these). Empty = straight-lining not run.
    grid_columns: list[str] = Field(default_factory=list, max_length=200)
    # A column holding a real start time (speeder check). None = speeder check not run.
    start_column: str | None = Field(default=None, max_length=300)


@router.post("/google-forms", status_code=status.HTTP_201_CREATED)
def import_google_forms(
    body: GoogleFormsImportIn,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Import one Google Form's responses as a raw dataset (M5.2). Needs the owner's token in settings."""
    settings = get_settings()
    token = settings.google_forms_access_token.get_secret_value() if settings.google_forms_access_token else ""
    if not token.strip():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "Google Forms import is not configured: set GOOGLE_FORMS_ACCESS_TOKEN.")
    try:
        form_id = survey_google_forms.check_form_id(body.form_id)
    except survey_google_forms.SurveyError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    try:
        columns, rows = survey_google_forms.fetch_table(form_id, token)
    except survey_google_forms.SurveyError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    csv_text = datasets.serialise(columns, rows)
    return _read(_store_raw(db, project, user, body.name, csv_text, source="google_forms"))


@router.post("/{dataset_id}/quality")
def quality(
    dataset_id: uuid.UUID,
    body: QualityIn,
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Rule-based response-quality flags (M5.2). Read-only: flags rows, never removes them."""
    version = _version(db, project, dataset_id)
    if not _intact(version):
        raise HTTPException(status.HTTP_409_CONFLICT, "Dataset no longer matches its stored hash; it cannot be checked.")
    _, columns, rows = datasets.parse(version.content.encode("utf-8"))
    try:
        report = response_quality.check(
            columns, rows, attention=body.attention, min_seconds=body.min_seconds,
            straightline_min_items=body.straightline_min_items,
            grid_columns=body.grid_columns, start_column=body.start_column,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return {"dataset_id": str(version.id), "sha256": version.sha256, **report}


@router.get("")
def list_versions(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    versions = db.scalars(
        select(DatasetVersion).where(DatasetVersion.project_id == project.id).order_by(DatasetVersion.version_no.asc())
    ).all()
    return [_read(v) for v in versions]


@router.get("/{dataset_id}")
def get_version(
    dataset_id: uuid.UUID,
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return _read(_version(db, project, dataset_id))


@router.get("/{dataset_id}/content")
def get_content(
    dataset_id: uuid.UUID,
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    version = _version(db, project, dataset_id)
    return {**_read(version), "csv_text": version.content}


@router.post("/{dataset_id}/clean", status_code=status.HTTP_201_CREATED)
def clean(
    dataset_id: uuid.UUID,
    body: CleanIn,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    parent = _version(db, project, dataset_id)
    if not _intact(parent):
        raise HTTPException(status.HTTP_409_CONFLICT, "This dataset no longer matches its hash, so it cannot be cleaned.")
    key = get_settings().pseudonym_key
    key_bytes = key.get_secret_value().encode("utf-8") if key else None
    if key_bytes is None and any(s.op == "pseudonymise_columns" for s in body.steps):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Pseudonymisation is not available: the server has no PSEUDONYM_KEY configured.",
        )
    try:
        _, columns, rows = datasets.parse(parent.content.encode("utf-8"))
        columns, rows, log = datasets.apply_cleaning(
            columns, rows, [s.model_dump(exclude_none=True) for s in body.steps], pseudonym_key=key_bytes
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    text = datasets.serialise(columns, rows)
    version = DatasetVersion(
        project_id=project.id,
        version_no=_next_version_no(db, project),
        parent_id=parent.id,
        kind="cleaned",
        name=body.name.strip(),
        content=text,
        sha256=datasets.sha256_hex(text),
        row_count=len(rows),
        column_names=columns,
        cleaning_steps=log,
        created_by=str(user.id),
    )
    db.add(version)
    db.flush()
    audit.record(
        db, actor=audit.user_actor(user), action="dataset.cleaned", project_id=project.id,
        payload={"dataset_id": str(version.id), "parent_id": str(parent.id), "sha256": version.sha256, "steps": [e["op"] for e in log]},
    )
    db.commit()
    db.refresh(version)
    return _read(version)


# ---- case exclusions (M5.7.1) and codebook (M5.7.2) -------------------------------------


class ExclusionRuleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column: str = Field(min_length=1, max_length=200)
    op: Literal["equals", "not_equals", "is_blank", "less_than", "greater_than"]
    value: str | None = Field(default=None, max_length=500)


class ExcludeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    rule_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)


def _rule_read(rule: ExclusionRule) -> dict[str, Any]:
    return {
        "id": str(rule.id),
        "dataset_id": str(rule.dataset_id),
        "rule": rule.rule_json,
        "rule_sha256": rule.rule_sha256,
        "status": rule.status,
        "intact": exclusion_rules.rule_sha256(rule.rule_json) == rule.rule_sha256,
        "proposed_by": rule.proposed_by,
        "approved_by": rule.approved_by,
        "approved_at": rule.approved_at.isoformat() if isinstance(rule.approved_at, datetime) else None,
    }


def _rule(db: Session, project: Project, dataset_id: uuid.UUID, rule_id: uuid.UUID) -> ExclusionRule:
    rule = db.get(ExclusionRule, rule_id)
    if rule is None or rule.project_id != project.id or rule.dataset_id != dataset_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Exclusion rule not found for this dataset.")
    return rule


@router.post("/{dataset_id}/exclusion-rules", status_code=status.HTTP_201_CREATED)
def propose_rule(
    dataset_id: uuid.UUID,
    body: ExclusionRuleIn,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    version = _version(db, project, dataset_id)
    if not _intact(version):
        raise HTTPException(status.HTTP_409_CONFLICT, "This dataset no longer matches its hash, so no rule can be proposed for it.")
    try:
        rule_json = exclusion_rules.normalise(body.model_dump(exclude_none=True), version.column_names)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    rule = ExclusionRule(
        project_id=project.id,
        dataset_id=version.id,
        rule_json=rule_json,
        rule_sha256=exclusion_rules.rule_sha256(rule_json),
        status="proposed",
        proposed_by=str(user.id),
    )
    db.add(rule)
    db.flush()
    audit.record(
        db, actor=audit.user_actor(user), action="exclusion_rule.proposed", project_id=project.id,
        payload={"rule_id": str(rule.id), "dataset_id": str(version.id), "rule_sha256": rule.rule_sha256},
    )
    db.commit()
    db.refresh(rule)
    return _rule_read(rule)


@router.get("/{dataset_id}/exclusion-rules")
def list_rules(
    dataset_id: uuid.UUID,
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    _version(db, project, dataset_id)
    rules = db.scalars(
        select(ExclusionRule)
        .where(ExclusionRule.project_id == project.id, ExclusionRule.dataset_id == dataset_id)
        .order_by(ExclusionRule.created_at.asc(), ExclusionRule.id.asc())
    ).all()
    return [_rule_read(rule) for rule in rules]


@router.post("/{dataset_id}/exclusion-rules/{rule_id}/approve")
def approve_rule(
    dataset_id: uuid.UUID,
    rule_id: uuid.UUID,
    project: Project = Depends(project_access(APPROVE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _version(db, project, dataset_id)
    rule = _rule(db, project, dataset_id, rule_id)
    if rule.status == "approved":
        return _rule_read(rule)  # idempotent: the first approval stands
    if exclusion_rules.rule_sha256(rule.rule_json) != rule.rule_sha256:
        raise HTTPException(status.HTTP_409_CONFLICT, "This rule no longer matches its hash, so it cannot be approved.")
    rule.status = "approved"
    rule.approved_by = str(user.id)
    rule.approved_at = utcnow()
    audit.record(
        db, actor=audit.user_actor(user), action="exclusion_rule.approved", project_id=project.id,
        payload={"rule_id": str(rule.id), "dataset_id": str(dataset_id), "rule_sha256": rule.rule_sha256},
    )
    db.commit()
    db.refresh(rule)
    return _rule_read(rule)


@router.post("/{dataset_id}/exclude", status_code=status.HTTP_201_CREATED)
def exclude(
    dataset_id: uuid.UUID,
    body: ExcludeIn,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Apply approved rules as a new cleaned version. Refused, and nothing is written, unless every rule is approved."""
    parent = _version(db, project, dataset_id)
    if not _intact(parent):
        raise HTTPException(status.HTTP_409_CONFLICT, "This dataset no longer matches its hash, so it cannot be cleaned.")
    if len(set(body.rule_ids)) != len(body.rule_ids):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "The same exclusion rule was listed more than once.")
    approved: list[dict[str, Any]] = []
    for rule_id in body.rule_ids:
        rule = _rule(db, project, dataset_id, rule_id)
        if rule.status != "approved":
            raise HTTPException(status.HTTP_409_CONFLICT, "Only approved exclusion rules can be applied.")
        if exclusion_rules.rule_sha256(rule.rule_json) != rule.rule_sha256:
            raise HTTPException(status.HTTP_409_CONFLICT, "An exclusion rule no longer matches its hash.")
        approved.append({"id": str(rule.id), "rule": rule.rule_json, "rule_sha256": rule.rule_sha256})
    try:
        _, columns, rows = datasets.parse(parent.content.encode("utf-8"))
        rows, log = exclusion_rules.apply(columns, rows, approved)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    text = datasets.serialise(columns, rows)
    version = DatasetVersion(
        project_id=project.id,
        version_no=_next_version_no(db, project),
        parent_id=parent.id,
        kind="cleaned",
        name=body.name.strip(),
        content=text,
        sha256=datasets.sha256_hex(text),
        row_count=len(rows),
        column_names=columns,
        cleaning_steps=log,
        created_by=str(user.id),
    )
    db.add(version)
    db.flush()
    audit.record(
        db, actor=audit.user_actor(user), action="dataset.excluded", project_id=project.id,
        payload={"dataset_id": str(version.id), "parent_id": str(parent.id), "sha256": version.sha256,
                 "rule_ids": [entry["id"] for entry in approved]},
    )
    db.commit()
    db.refresh(version)
    return _read(version)


@router.get("/{dataset_id}/codebook")
def get_codebook(
    dataset_id: uuid.UUID,
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    version = _version(db, project, dataset_id)
    if not _intact(version):
        raise HTTPException(status.HTTP_409_CONFLICT, "This dataset no longer matches its hash, so no codebook is produced.")
    _, columns, rows = datasets.parse(version.content.encode("utf-8"))
    return {
        "dataset_id": str(version.id),
        "source_sha256": version.sha256,
        "variables": codebook.build(columns, rows),
        "note": "Definitions are left blank for the researcher to write.",
    }
