"""Scholar-editable copies of extraction schemas (plan M3.1.2)."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.extraction_schema import load_schema
from app.main import app
from app.models import AuditEvent, ProjectExtractionSchema


def _login(email):
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": email, "display_name": "X"})
    return client


@pytest.fixture
def world():
    tag = uuid.uuid4().hex[:8]
    owner = _login(f"schema-owner-{tag}@example.com")
    pid = owner.post("/api/projects", json={"title": "Schemas"}).json()["id"]
    sid = owner.post(f"/api/projects/{pid}/sources", json={"title": "A paper"}).json()["id"]
    return {"c": owner, "pid": pid, "sid": sid, "base": f"/api/projects/{pid}/extraction-schemas"}


SAMPLE_SIZE = {"key": "sample_size", "type": "integer", "description": "Number of participants", "critical": True}


def copy(world, name="lab", **extra):
    return world["c"].post(world["base"], json={"name": name, **extra})


def edit(world, name, fields, label="Lab schema", description="Our fields"):
    return world["c"].put(f"{world['base']}/{name}", json={"label": label, "description": description, "fields": fields})


def extract(world, schema_name, fields, evidence):
    return world["c"].post(
        f"/api/projects/{world['pid']}/extractions",
        json={"source_id": world["sid"], "schema_name": schema_name, "fields": fields, "evidence": evidence},
    )


def test_list_shows_built_ins_and_no_project_schemas_at_first(world):
    data = world["c"].get(world["base"]).json()
    default = next(s for s in data["built_in"] if s["ref"] == "default")
    assert default["kind"] == "built_in" and default["schema_version"] == "default@1" and default["field_count"] == 9
    assert data["project"] == []


def test_copy_of_default_is_version_one_with_the_same_fields(world):
    r = copy(world)
    assert r.status_code == 201, r.text
    body = r.json()
    default = load_schema("default")
    assert body["ref"] == "project:lab" and body["version"] == 1 and body["schema_version"] == "project:lab@1"
    assert body["based_on"] == "default@1" and body["label"] == f"{default.label} (copy)"
    assert [f["key"] for f in body["fields"]] == [f.key for f in default.fields]
    assert [s["ref"] for s in world["c"].get(world["base"]).json()["project"]] == ["project:lab"]


def test_copy_of_default_preserves_nested_value_validation(world):
    assert copy(world).status_code == 201
    response = extract(
        world,
        "project:lab",
        {"sample": {"n": "many", "population": [], "country": {}}},
        {"sample": {"quote": "Participants were recruited", "page": 2}},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == [
        "sample.country: expected string",
        "sample.n: expected integer",
        "sample.population: expected string",
    ]
    missing_property = extract(
        world,
        "project:lab",
        {"sample": {}},
        {"sample": {"quote": "Participants were recruited", "page": 2}},
    )
    assert missing_property.status_code == 422
    assert missing_property.json()["detail"] == [
        "sample.country: missing property",
        "sample.n: missing property",
        "sample.population: missing property",
    ]


def test_copy_name_rules(world):
    assert copy(world, name="default").status_code == 409  # can't shadow a built-in
    assert copy(world).status_code == 201
    assert copy(world).status_code == 409  # already exists
    for bad in ("Lab", "x", "has-dash", "project:lab", "1abc"):
        assert copy(world, name=bad).status_code == 422, bad
    assert copy(world, name="other", based_on="nope").status_code == 422
    assert copy(world, name="other", based_on="project:missing").status_code == 422


def test_edit_adds_a_version_and_keeps_the_old_one(world):
    copy(world)
    r = edit(world, "lab", [SAMPLE_SIZE])
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 2 and [f["key"] for f in r.json()["fields"]] == ["sample_size"]
    old = world["c"].get(f"{world['base']}/lab", params={"version": 1}).json()
    assert old["version"] == 1 and old["field_count"] == 9
    assert world["c"].get(f"{world['base']}/lab").json()["version"] == 2
    listed = world["c"].get(world["base"]).json()["project"]
    assert [(s["ref"], s["version"]) for s in listed] == [("project:lab", 2)]
    assert world["c"].get(f"{world['base']}/lab", params={"version": 9}).status_code == 404


def test_saving_unchanged_content_adds_no_version(world):
    copy(world)
    edit(world, "lab", [SAMPLE_SIZE])
    assert edit(world, "lab", [SAMPLE_SIZE]).json()["version"] == 2
    with SessionLocal() as db:
        assert len(db.scalars(select(ProjectExtractionSchema).where(ProjectExtractionSchema.project_id == uuid.UUID(world["pid"]))).all()) == 2


def test_invalid_edits_are_refused(world):
    copy(world)
    assert edit(world, "lab", []).status_code == 422
    assert edit(world, "lab", [SAMPLE_SIZE, SAMPLE_SIZE]).status_code == 422
    assert edit(world, "lab", [{**SAMPLE_SIZE, "type": "date"}]).status_code == 422
    assert edit(world, "lab", [{**SAMPLE_SIZE, "key": "Bad Key"}]).status_code == 422
    assert edit(world, "lab", [{"key": f"f{i:03d}", "type": "string", "description": "d"} for i in range(101)]).status_code == 422
    assert edit(world, "default", [SAMPLE_SIZE]).status_code == 409  # built-ins are read-only
    assert edit(world, "missing", [SAMPLE_SIZE]).status_code == 404
    assert world["c"].get(f"{world['base']}/lab").json()["version"] == 1


def test_built_in_schema_can_be_read_by_name(world):
    body = world["c"].get(f"{world['base']}/default").json()
    assert body["kind"] == "built_in" and len(body["fields"]) == 9
    assert world["c"].get(f"{world['base']}/default", params={"version": 2}).status_code == 404


def test_extractions_use_the_project_schema_and_record_its_version(world):
    copy(world)
    edit(world, "lab", [SAMPLE_SIZE])
    ok = extract(world, "project:lab", {"sample_size": 120}, {"sample_size": {"quote": "120 clerks took part", "page": 3}})
    assert ok.status_code == 201, ok.text
    assert ok.json()["schema_version"] == "project:lab@2"
    # a default-schema field isn't part of the edited copy, and a wrong type is still caught
    assert extract(world, "project:lab", {"research_question": "Why?"}, {"research_question": {"quote": "q", "page": 1}}).status_code == 422
    assert extract(world, "project:lab", {"sample_size": "many"}, {"sample_size": {"quote": "q", "page": 1}}).status_code == 422
    # evidence is still required for a non-null field
    assert extract(world, "project:lab", {"sample_size": 5}, {}).status_code == 422
    assert extract(world, "project:missing", {"sample_size": 5}, {}).status_code == 422


def test_an_extraction_made_with_an_older_version_must_be_redone(world):
    copy(world)
    edit(world, "lab", [SAMPLE_SIZE])
    row = extract(world, "project:lab", {"sample_size": 1}, {"sample_size": {"quote": "one", "page": 1}}).json()
    edit(world, "lab", [SAMPLE_SIZE, {"key": "country", "type": "string", "description": "Country"}])
    r = world["c"].put(
        f"/api/projects/{world['pid']}/extractions/{row['id']}",
        json={"fields": {"sample_size": 2}, "evidence": {"sample_size": {"quote": "two", "page": 1}}},
    )
    assert r.status_code == 409 and "project:lab@2" in r.json()["detail"]


def test_stale_extraction_update_returns_conflict_before_new_schema_validation(world):
    copy(world)
    row = extract(
        world,
        "project:lab",
        {"research_question": "Why?"},
        {"research_question": {"quote": "The paper asks why", "page": 1}},
    ).json()
    edit(world, "lab", [SAMPLE_SIZE])

    response = world["c"].put(
        f"/api/projects/{world['pid']}/extractions/{row['id']}",
        json={
            "fields": {"research_question": "Updated question"},
            "evidence": {"research_question": {"quote": "The paper asks why", "page": 1}},
        },
    )

    assert response.status_code == 409
    assert "project:lab@1" in response.json()["detail"]
    assert "project:lab@2" in response.json()["detail"]


def test_a_project_schema_can_be_copied_again(world):
    copy(world)
    edit(world, "lab", [SAMPLE_SIZE])
    r = copy(world, name="lab_v2", based_on="project:lab", label="Second")
    assert r.status_code == 201 and r.json()["based_on"] == "project:lab@2" and r.json()["label"] == "Second"


def test_project_schemas_are_private_to_their_project(world):
    copy(world)
    other = world["c"].post("/api/projects", json={"title": "Other"}).json()["id"]
    other_sid = world["c"].post(f"/api/projects/{other}/sources", json={"title": "P"}).json()["id"]
    assert world["c"].get(f"/api/projects/{other}/extraction-schemas/lab").status_code == 404
    r = world["c"].post(
        f"/api/projects/{other}/extractions",
        json={"source_id": other_sid, "schema_name": "project:lab", "fields": {}, "evidence": {}},
    )
    assert r.status_code == 422
    assert world["c"].post(f"/api/projects/{other}/extraction-schemas", json={"name": "lab"}).status_code == 201


def test_copy_and_edit_are_audited(world):
    copy(world)
    edit(world, "lab", [SAMPLE_SIZE, {"key": "method", "type": "object", "description": "Changed wording"}])
    with SessionLocal() as db:
        events = db.scalars(
            select(AuditEvent).where(AuditEvent.project_id == uuid.UUID(world["pid"]), AuditEvent.action.like("extraction_schema.%"))
            .order_by(AuditEvent.action)  # "created" sorts before "revised"
        ).all()
    created, revised = [e.payload_json for e in events]
    assert created["schema"] == "project:lab" and created["based_on"] == "default@1" and len(created["fields"]) == 9
    assert revised["version"] == 2 and revised["fields_added"] == ["sample_size"]
    assert "research_question" in revised["fields_removed"] and revised["fields_changed"] == ["method"]


def test_reviewer_can_read_but_not_copy(world):
    from app.models import ProjectMember, ProjectRole

    tag = uuid.uuid4().hex[:8]
    reviewer = _login(f"schema-reviewer-{tag}@example.com")
    user_id = reviewer.get("/api/auth/me").json()["id"]
    with SessionLocal() as db:
        db.add(ProjectMember(project_id=uuid.UUID(world["pid"]), user_id=uuid.UUID(user_id), role=ProjectRole.reviewer))
        db.commit()
    assert reviewer.get(world["base"]).status_code == 200
    assert reviewer.post(world["base"], json={"name": "mine"}).status_code == 403
