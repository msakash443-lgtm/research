"""Scholar-editable extraction schemas (plan M3.1.2, spec 5.4.1).

The built-in schemas (`app/extraction_schemas/*.json`, `default` = Appendix B) are read-only. A project
copies one into its own named schema and edits the copy; every edit is a new immutable version.

A project schema is referred to as `project:<name>`, so it can never be mistaken for a built-in, and an
extraction made with it records `project:<name>@<version>`. `resolve` turns either kind of reference
into an `ExtractionSchema`, so `check_extraction` treats both the same.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.extraction_schema import ExtractionSchema, FieldSpec, SchemaError, available_schemas, load_schema
from app.models import ProjectExtractionSchema

PREFIX = "project:"
MAX_SCHEMAS_PER_PROJECT = 50
MAX_FIELDS = 100


def ref(name: str) -> str:
    return f"{PREFIX}{name}"


def to_schema(row: ProjectExtractionSchema) -> ExtractionSchema:
    return ExtractionSchema(
        name=ref(row.name), version=row.version, label=row.label, description=row.description,
        fields=[FieldSpec(**f) for f in row.fields],
    )


def validate_fields(fields: list[FieldSpec]) -> list[dict]:
    """Field specs as stored; raises `SchemaError` on an empty, oversized or repeated-key list."""
    if not fields:
        raise SchemaError("A schema needs at least one field")
    if len(fields) > MAX_FIELDS:
        raise SchemaError(f"A schema can have at most {MAX_FIELDS} fields")
    keys = [f.key for f in fields]
    repeated = sorted({k for k in keys if keys.count(k) > 1})
    if repeated:
        raise SchemaError(f"Field keys must be unique: {', '.join(repeated)}")
    return [f.model_dump() for f in fields]


def latest(db: Session, project_id, name: str) -> ProjectExtractionSchema | None:
    return db.scalar(
        select(ProjectExtractionSchema)
        .where(ProjectExtractionSchema.project_id == project_id, ProjectExtractionSchema.name == name)
        .order_by(ProjectExtractionSchema.version.desc())
        .limit(1)
    )


def version(db: Session, project_id, name: str, number: int) -> ProjectExtractionSchema | None:
    return db.scalar(
        select(ProjectExtractionSchema).where(
            ProjectExtractionSchema.project_id == project_id,
            ProjectExtractionSchema.name == name,
            ProjectExtractionSchema.version == number,
        )
    )


def latest_rows(db: Session, project_id) -> list[ProjectExtractionSchema]:
    """The newest version of each of the project's schemas, by name."""
    newest = (
        select(ProjectExtractionSchema.name, func.max(ProjectExtractionSchema.version).label("v"))
        .where(ProjectExtractionSchema.project_id == project_id)
        .group_by(ProjectExtractionSchema.name)
        .subquery()
    )
    return list(
        db.scalars(
            select(ProjectExtractionSchema)
            .join(newest, (ProjectExtractionSchema.name == newest.c.name) & (ProjectExtractionSchema.version == newest.c.v))
            .where(ProjectExtractionSchema.project_id == project_id)
            .order_by(ProjectExtractionSchema.name)
        )
    )


def count(db: Session, project_id) -> int:
    return db.scalar(
        select(func.count(func.distinct(ProjectExtractionSchema.name))).where(ProjectExtractionSchema.project_id == project_id)
    ) or 0


def is_builtin(name: str) -> bool:
    return name in available_schemas()


def resolve(db: Session, project_id, reference: str) -> ExtractionSchema:
    """The current schema for a built-in name or a `project:<name>` reference; `SchemaError` if none."""
    if reference.startswith(PREFIX):
        row = latest(db, project_id, reference[len(PREFIX):])
        if row is None:
            raise SchemaError(f"Extraction schema {reference!r} does not exist in this project")
        return to_schema(row)
    return load_schema(reference)
