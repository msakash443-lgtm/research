"""Extraction schemas API (plan M3.1.2, spec 5.4.1): list the schemas a project can extract with, and let
its owner/co-authors copy a schema and edit the copy.

Built-in schemas never change here. A copy is a project schema (`project:<name>`); each edit stores a new
version and leaves the earlier ones, so existing extractions stay tied to the fields they were made with.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import audit, project_schemas
from app.database import get_db
from app.dependencies import READ_ROLES, WRITE_ROLES, current_user, project_access
from app.extraction_schema import _ID, ExtractionSchema, FieldSpec, SchemaError, available_schemas, load_schema, schema_version
from app.models import Project, ProjectExtractionSchema, User

router = APIRouter(prefix="/projects/{project_id}/extraction-schemas", tags=["extraction-schemas"])


class SchemaCopy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=_ID.pattern, description="Lower-case letters, digits and _; becomes `project:<name>`")
    based_on: str = Field(default="default", max_length=80, description="A built-in name or `project:<name>`")
    label: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class SchemaEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=200)
    description: str = Field(max_length=2000)
    fields: list[FieldSpec]


def _summary(schema: ExtractionSchema, kind: str, **extra: Any) -> dict[str, Any]:
    return {
        "ref": schema.name,
        "kind": kind,
        "version": schema.version,
        "schema_version": schema_version(schema),
        "label": schema.label,
        "description": schema.description,
        "field_count": len(schema.fields),
        **extra,
    }


def _detail(schema: ExtractionSchema, kind: str, **extra: Any) -> dict[str, Any]:
    return {**_summary(schema, kind, **extra), "fields": [f.model_dump() for f in schema.fields]}


def _row_extra(row: ProjectExtractionSchema) -> dict[str, Any]:
    return {"name": row.name, "based_on": row.based_on, "created_by": row.created_by, "created_at": row.created_at}


def _project_row(db: Session, project: Project, name: str, number: int | None) -> ProjectExtractionSchema:
    row = (
        project_schemas.version(db, project.id, name, number)
        if number is not None
        else project_schemas.latest(db, project.id, name)
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Extraction schema not found")
    return row


@router.get("")
def list_schemas(project: Project = Depends(project_access(READ_ROLES)), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Built-in schemas (read-only) and the newest version of each of the project's own."""
    return {
        "built_in": [_summary(load_schema(n), "built_in") for n in available_schemas()],
        "project": [
            _summary(project_schemas.to_schema(row), "project", **_row_extra(row))
            for row in project_schemas.latest_rows(db, project.id)
        ],
    }


@router.get("/{schema_name}")
def get_schema(
    schema_name: str,
    version: int | None = Query(default=None, ge=1),
    project: Project = Depends(project_access(READ_ROLES)),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """One schema with its fields. A built-in by its name; a project schema by its name (any version)."""
    if project_schemas.is_builtin(schema_name):
        schema = load_schema(schema_name)
        if version is not None and version != schema.version:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Only the current version of a built-in schema is available")
        return _detail(schema, "built_in")
    row = _project_row(db, project, schema_name, version)
    return _detail(project_schemas.to_schema(row), "project", **_row_extra(row))


@router.post("", status_code=status.HTTP_201_CREATED)
def copy_schema(
    payload: SchemaCopy,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Copy a built-in or project schema into a new project schema (version 1) that can then be edited."""
    if project_schemas.is_builtin(payload.name):
        raise HTTPException(status.HTTP_409_CONFLICT, f"{payload.name!r} is a built-in schema name; choose another")
    if project_schemas.latest(db, project.id, payload.name) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"This project already has a schema named {payload.name!r}")
    if project_schemas.count(db, project.id) >= project_schemas.MAX_SCHEMAS_PER_PROJECT:
        raise HTTPException(status.HTTP_409_CONFLICT, f"A project can have at most {project_schemas.MAX_SCHEMAS_PER_PROJECT} schemas")
    try:
        source = project_schemas.resolve(db, project.id, payload.based_on)
    except SchemaError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    actor = audit.user_actor(user)
    row = ProjectExtractionSchema(
        project_id=project.id, name=payload.name, version=1,
        label=payload.label or f"{source.label} (copy)",
        description=source.description if payload.description is None else payload.description,
        fields=[f.model_dump() for f in source.fields], based_on=schema_version(source), created_by=actor,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError as exc:  # a concurrent copy with the same name
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, f"This project already has a schema named {payload.name!r}") from exc
    audit.record(
        db, actor=actor, action="extraction_schema.created", project_id=project.id,
        payload={"schema": project_schemas.ref(row.name), "version": 1, "based_on": row.based_on,
                 "fields": [f["key"] for f in row.fields]},
    )
    db.commit()
    db.refresh(row)
    return _detail(project_schemas.to_schema(row), "project", **_row_extra(row))


@router.put("/{schema_name}")
def edit_schema(
    schema_name: str,
    payload: SchemaEdit,
    project: Project = Depends(project_access(WRITE_ROLES)),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Save an edited project schema as its next version. Built-ins can't be edited; copy one first.

    Sending the current content again changes nothing and returns the current version.
    """
    if project_schemas.is_builtin(schema_name):
        raise HTTPException(status.HTTP_409_CONFLICT, "Built-in schemas can't be edited; copy it into a project schema first")
    current = _project_row(db, project, schema_name, None)
    try:
        fields = project_schemas.validate_fields(payload.fields)
    except SchemaError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    if (fields, payload.label, payload.description) == (current.fields, current.label, current.description):
        return _detail(project_schemas.to_schema(current), "project", **_row_extra(current))
    actor = audit.user_actor(user)
    row = ProjectExtractionSchema(
        project_id=project.id, name=current.name, version=current.version + 1, label=payload.label,
        description=payload.description, fields=fields, based_on=current.based_on, created_by=actor,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError as exc:  # someone else saved a version at the same moment
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "The schema was changed by someone else; reload it and try again") from exc
    before, after = {f["key"] for f in current.fields}, {f["key"] for f in fields}
    audit.record(
        db, actor=actor, action="extraction_schema.revised", project_id=project.id,
        payload={"schema": project_schemas.ref(row.name), "version": row.version, "previous_version": current.version,
                 "fields_added": sorted(after - before), "fields_removed": sorted(before - after),
                 "fields_changed": sorted(k for k in before & after
                                          if next(f for f in current.fields if f["key"] == k) != next(f for f in fields if f["key"] == k))},
    )
    db.commit()
    db.refresh(row)
    return _detail(project_schemas.to_schema(row), "project", **_row_extra(row))
