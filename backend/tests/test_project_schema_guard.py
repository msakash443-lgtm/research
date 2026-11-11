"""Versioned project extraction schemas must be append-only (M3.1.2)."""

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DatabaseError

from app.database import SessionLocal
from app.models import Project, ProjectExtractionSchema, User


def _schema_id():
    with SessionLocal() as db:
        owner = User(email=f"schema-{uuid.uuid4().hex}@example.test")
        db.add(owner)
        db.flush()
        project = Project(owner_id=owner.id, title="Schemas")
        db.add(project)
        db.flush()
        row = ProjectExtractionSchema(
            project_id=project.id,
            name="lab",
            version=1,
            label="Lab schema",
            description="",
            fields=[],
            based_on="default@1",
            created_by="user:test",
        )
        db.add(row)
        db.commit()
        return row.id


def test_orm_update_of_a_project_schema_version_is_rejected():
    schema_id = _schema_id()
    with SessionLocal() as db:
        row = db.get(ProjectExtractionSchema, schema_id)
        row.label = "Tampered"
        with pytest.raises(ValueError, match="append-only"):
            db.commit()
        db.rollback()
        assert db.get(ProjectExtractionSchema, schema_id).label == "Lab schema"


@pytest.mark.parametrize("statement", [
    "UPDATE project_extraction_schemas SET label = 'Tampered' WHERE id = :id",
    "DELETE FROM project_extraction_schemas WHERE id = :id",
])
def test_raw_sql_cannot_rewrite_or_delete_a_project_schema_version(statement):
    schema_id = _schema_id()
    with SessionLocal() as db:
        with pytest.raises(DatabaseError, match="append-only"):
            db.execute(text(statement), {"id": schema_id})
            db.commit()
        db.rollback()
        assert db.scalar(select(ProjectExtractionSchema.id).where(ProjectExtractionSchema.id == schema_id)) == schema_id
