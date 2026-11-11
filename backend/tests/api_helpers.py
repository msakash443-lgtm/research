"""Setup helpers shared by the test files (X.179): sign in a development user, create a project or a source.

Plain functions, not fixtures: most callers are module-level helpers and parametrised cases that cannot
take a fixture argument. Importing this module is safe (conftest.py is not imported: it would run twice).
Every helper asserts the response it depends on, so a broken setup fails where it happens.
"""

import uuid

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import AnalysisPlan, AnalysisRun, DatasetVersion, Project, ScreeningDecision, Source, User


def sign_in(email: str, display_name: str = "Tester") -> TestClient:
    """A TestClient signed in as `email` through the development login."""
    client = TestClient(app)
    response = client.post("/api/auth/development/login", json={"email": email, "display_name": display_name})
    assert response.status_code == 200, response.text
    client.user_id = response.json()["id"]
    return client


def sign_in_user(email: str, display_name: str = "Tester") -> tuple[TestClient, str]:
    """`(client, user_id)` for a development user signed in as `email`."""
    client = sign_in(email, display_name)
    return client, client.user_id


def create_project(client: TestClient, title: str = "Project", **fields) -> str:
    """Id of a new project created through the API as `client`'s user."""
    response = client.post("/api/projects", json={"title": title, **fields})
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


def create_source(client: TestClient, project_id: str, title: str = "Paper", **fields) -> str:
    """Id of a new source created through the API."""
    response = client.post(f"/api/projects/{project_id}/sources", json={"title": title, **fields})
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


def include_at_full_text(project_id: str, *source_ids: str) -> None:
    """Record a final full-text include decision for each source (extraction accepts only those, X.155/X.202)."""
    with SessionLocal() as db:
        for source_id in source_ids:
            db.add(ScreeningDecision(project_id=uuid.UUID(project_id), source_id=uuid.UUID(source_id), stage="full_text", seq=1,
                                     decision="include", decided_by="human", decider="test-setup"))
        db.commit()


def create_included_source(client: TestClient, project_id: str, title: str = "Paper", **fields) -> str:
    """Id of a new source that already has a final full-text include decision."""
    source_id = create_source(client, project_id, title, **fields)
    include_at_full_text(project_id, source_id)
    return source_id


def insert_project(title: str = "Project") -> uuid.UUID:
    """Id of a project and its owner inserted straight into the database (no HTTP, no session)."""
    with SessionLocal() as db:
        user = User(email=f"owner-{uuid.uuid4().hex}@example.test")
        db.add(user)
        db.flush()
        project = Project(owner_id=user.id, title=title)
        db.add(project)
        db.commit()
        return project.id


def insert_source(project_id: str, **fields) -> str:
    """Id of a source inserted straight into the database."""
    with SessionLocal() as db:
        row = Source(project_id=uuid.UUID(project_id), **fields)
        db.add(row)
        db.commit()
        return str(row.id)


def insert_analysis_run(project_id: str, output: dict, user_id: str = "u1") -> str:
    """Id of a recorded analysis run (with its dataset and plan) whose `output_json` is `output`."""
    with SessionLocal() as db:
        dataset = DatasetVersion(
            project_id=uuid.UUID(project_id), version_no=1, kind="raw", name="d.csv", content="a\n1\n", sha256="0" * 64,
            row_count=1, column_names=["a"], created_by=user_id,
        )
        db.add(dataset)
        db.flush()
        plan = AnalysisPlan(
            project_id=uuid.UUID(project_id), dataset_id=dataset.id, spec_json={"tests": [], "variables": [], "seed": 0},
            plan_sha256="1" * 64, environment_sha256="2" * 64, created_by=user_id,
        )
        db.add(plan)
        db.flush()
        run = AnalysisRun(
            project_id=uuid.UUID(project_id), analysis_plan_id=plan.id, dataset_id=dataset.id, plan_sha256=plan.plan_sha256,
            dataset_sha256=dataset.sha256, script_sha256="3" * 64, environment_sha256=plan.environment_sha256,
            image="python@sha256:" + "4" * 64, output_json=output, created_by=user_id,
        )
        db.add(run)
        db.commit()
        return str(run.id)


def start_research_run(client: TestClient, project_id: str, question: str = "Did output rise at all?") -> dict:
    """The accepted research run created through the API."""
    response = client.post(f"/api/projects/{project_id}/research-runs", json={"question": question})
    assert response.status_code == 202, response.text
    return response.json()
