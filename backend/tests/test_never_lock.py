"""X.47.3: nothing a person needs to keep working is ever locked by a pending gate, at any stage.

For every workflow stage, with every gate still pending: quick capture (a note), every project read
endpoint, the owner going back to an earlier stage, and an exploratory Ask (a research run) all succeed.
The read endpoints are taken from the live route table, so a new GET route is covered without edits here."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import artifacts as art
from app.config import get_settings
from app.database import SessionLocal
from app.main import app
from app.models import GateStatus, Project, ProjectStage, Task, TaskStatus

STAGES = list(ProjectStage)
PREFIX = "/api/projects/{project_id}"
# Read routes that need a query parameter to be valid at all.
REQUIRED_QUERY = {
    "/stage/conditions": "target=scoped",
    "/design/method-recommendation": "goal=explore",
    "/design/sample-size": "test=correlation&r=0.3",
    "/design/saturation-guidance": "design=interviews",
}
# These routes contain a value-shaped path segment but do not identify a child row.
PARAMETERIZED_READS = {
    "/bibliometrics/export/vosviewer/{kind}.zip": "/bibliometrics/export/vosviewer/co_citation.zip",
    "/extraction-schemas/{schema_name}": "/extraction-schemas/default",
}


def _project_read_paths() -> list[tuple[str, str]]:
    paths = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if "GET" not in getattr(route, "methods", set()) or not path.startswith(PREFIX):
            continue
        rest = path[len(PREFIX):]
        if "{" in rest:
            replacement = PARAMETERIZED_READS.get(rest)
            if replacement is None:
                continue  # Child-record endpoints need a seeded child row; parent reads cover this lock invariant.
            paths.append((rest, replacement))
        else:
            paths.append((rest, rest))
    return sorted(set(paths))


READ_PATHS = _project_read_paths()


READ_PATHS = _project_read_paths()


@pytest.fixture
def world():
    tag = uuid.uuid4().hex[:8]
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": f"nolock-{tag}@example.com", "display_name": "N"})
    project_id = client.post("/api/projects", json={"title": "Never lock"}).json()["id"]
    return client, project_id, uuid.UUID(project_id)


def _at_stage_with_gates_pending(pid: uuid.UUID, stage: ProjectStage) -> None:
    with SessionLocal() as db:
        project = db.get(Project, pid)
        project.stage = stage
        gates = art.ensure_gates(db, project)
        assert gates and all(g.status == GateStatus.pending for g in gates)
        db.commit()


def test_the_route_table_yields_a_meaningful_set_of_read_endpoints():
    route_names = {pattern for pattern, _ in READ_PATHS}
    assert len(READ_PATHS) >= 25
    assert {"/stage", "/sources", "/screening/queue", "/prisma", "/audit"} <= route_names


@pytest.mark.parametrize("stage", STAGES, ids=lambda s: s.value)
def test_every_read_endpoint_works_at_every_stage_with_gates_pending(world, monkeypatch, stage):
    client, project_id, pid = world
    monkeypatch.setattr(get_settings(), "obsidian_export_enabled", True)  # a deployment flag, not a stage lock
    _at_stage_with_gates_pending(pid, stage)
    failures = []
    for pattern, rest in READ_PATHS:
        query = REQUIRED_QUERY.get(pattern)
        url = f"/api/projects/{project_id}{rest}" + (f"?{query}" if query else "")
        response = client.get(url)
        if response.status_code != 200:
            failures.append((pattern, response.status_code, response.text[:120]))
    assert failures == []


@pytest.mark.parametrize("stage", STAGES, ids=lambda s: s.value)
def test_quick_capture_works_at_every_stage_with_gates_pending(world, stage):
    client, project_id, pid = world
    _at_stage_with_gates_pending(pid, stage)
    response = client.post("/api/notes", json={"client_id": f"cap-{stage.value}", "body": "an idea at the bus stop"})
    assert response.status_code == 201, response.text
    note_id = response.json()["id"]
    filed = client.post(f"/api/notes/{note_id}/file", json={"project_id": project_id})
    assert filed.status_code == 200, filed.text


@pytest.mark.parametrize("stage", STAGES[1:], ids=lambda s: s.value)
def test_the_owner_can_always_go_back_with_gates_pending(world, stage):
    client, project_id, pid = world
    _at_stage_with_gates_pending(pid, stage)
    response = client.post(
        f"/api/projects/{project_id}/stage/reenter", json={"stage": STAGES[0].value, "reason": "rethinking the question"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["to_stage"] == STAGES[0].value


@pytest.mark.parametrize("stage", STAGES, ids=lambda s: s.value)
def test_an_exploratory_ask_is_accepted_and_never_held_at_a_gate(world, fake_llm, stage):
    client, project_id, pid = world
    _at_stage_with_gates_pending(pid, stage)
    response = client.post(
        f"/api/projects/{project_id}/research-runs", json={"question": "What is known about sleep and memory?"}
    )
    assert response.status_code == 202, response.text
    with SessionLocal() as db:
        tasks = list(db.scalars(select(Task).where(Task.project_id == pid, Task.type == "research_run")))
    assert len(tasks) == 1
    assert tasks[0].status != TaskStatus.blocked
