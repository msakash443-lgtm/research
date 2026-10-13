"""Current extraction review queue and the G4 readiness condition."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import project_schemas
from app.extraction_schema import SchemaError, schema_version
from app.models import Extraction, Project
from app.thematic_clusters import clusterable_sources


def current_rows(db: Session, project: Project) -> list[tuple[object, Extraction | None]]:
    """One newest extraction per active paper; a later edit supersedes an earlier review."""
    sources = clusterable_sources(db, project)
    chosen = {}
    for row in db.scalars(select(Extraction).where(Extraction.project_id == project.id)):
        current = chosen.get(row.source_id)
        rank = lambda item: (item.updated_at or item.created_at, str(item.id))
        if current is None or rank(row) > rank(current):
            chosen[row.source_id] = row
    return [(source, chosen.get(source.id)) for source in sources]


def schema_for_row(db: Session, project: Project, row: Extraction):
    name = row.schema_version.rpartition("@")[0]
    schema = project_schemas.resolve(db, project.id, name)
    if schema_version(schema) != row.schema_version:
        raise SchemaError(f"Extraction {row.id} uses an old schema version; create a new extraction")
    return schema


def g4_problem(db: Session, project: Project) -> str | None:
    current = current_rows(db, project)
    for source, row in current:
        if row is None:
            return f"G4 waits for an extraction of source {source.id}"
        try:
            schema_for_row(db, project, row)
        except SchemaError:
            return f"G4 waits for a current-schema extraction of source {source.id}"
        if not row.verified_by_human:
            return f"G4 waits for human verification of source {source.id}"
    return None




