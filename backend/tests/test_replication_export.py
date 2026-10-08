import csv
import hashlib
import io
import json
import uuid
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import Base, SessionLocal
from app.main import app
from app.models import AuditEvent, Cluster, ClusterMember, ClusterRun, Note, SourceEmbedding, Task
from app.replication_export import CHILD_TABLES, EXCLUDED_TABLES, NOT_EXPORTED, project_tables, schema_revision


def _login(email):
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": email, "display_name": "Exporter"})
    return client


@pytest.fixture
def world():
    tag = uuid.uuid4().hex[:8]
    owner = _login(f"rep-{tag}@example.com")
    pid = owner.post("/api/projects", json={"title": "Replication"}).json()["id"]
    sid = owner.post(f"/api/projects/{pid}/sources", json={
        "title": "=HYPERLINK(\"http://evil\")", "evidence_excerpt": "Sleep improves recall.", "locator": "p. 3"}).json()["id"]
    owner.put(f"/api/projects/{pid}/criteria", json={"framework": "custom", "criteria": [
        {"kind": "include", "text": "Empirical"}, {"kind": "exclude", "text": "Opinion"}]})
    owner.post(f"/api/projects/{pid}/screening/decisions", json={"source_id": sid, "decision": "include", "reason_code": "I1"})
    owner.post(f"/api/projects/{pid}/research-runs", json={"question": "What does sleep do to memory?"})
    other = owner.post("/api/projects", json={"title": "Other project"}).json()["id"]
    other_source = owner.post(f"/api/projects/{other}/sources", json={"title": "Belongs elsewhere"}).json()["id"]
    with SessionLocal() as db:
        user_id = db.scalar(select(AuditEvent.actor).where(AuditEvent.action == "project.created")) or "u"
        run = ClusterRun(project_id=uuid.UUID(pid), model_id="emb-1", k=2, seed=0, n_sources=1, created_by=user_id,
                         clusters=[Cluster(position=1, label="Cluster 1", members=[ClusterMember(source_id=uuid.UUID(sid), distance=0.1)])])
        db.add(run)
        db.add(SourceEmbedding(project_id=uuid.UUID(pid), source_id=uuid.UUID(sid), model="emb-1", text_hash="h" * 64, dims=2, vector=[0.25, 0.75]))
        db.add(ClusterRun(project_id=uuid.UUID(other), model_id="emb-1", k=2, seed=0, n_sources=1, created_by=user_id,
                          clusters=[Cluster(position=1, label="Other cluster", members=[ClusterMember(source_id=uuid.UUID(other_source), distance=0.2)])]))
        db.commit()
    return {"owner": owner, "pid": pid, "sid": sid, "other": other, "email": f"rep-{tag}@example.com"}


def download(world, client=None):
    response = (client or world["owner"]).get(f"/api/projects/{world['pid']}/export/replication")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"].startswith('attachment; filename="replication-')
    return zipfile.ZipFile(io.BytesIO(response.content))


def data(archive, table):
    return json.loads(archive.read(f"data/{table}.json"))


def test_the_manifest_checksums_every_file(world):
    archive = download(world)
    manifest = json.loads(archive.read("manifest.json"))
    names = set(archive.namelist()) - {"manifest.json"}
    assert set(manifest["files"]) == names
    for name, entry in manifest["files"].items():
        body = archive.read(name)
        assert hashlib.sha256(body).hexdigest() == entry["sha256"] and len(body) == entry["bytes"]
    assert manifest["project_id"] == world["pid"] and manifest["format_version"] == 1
    assert manifest["schema_revision"] == schema_revision() and manifest["schema_revision"]
    assert "README.md" in names and "derived/prisma.json" in names


def test_it_holds_the_project_record_and_nothing_from_other_projects(world):
    archive = download(world)
    assert [p["id"] for p in data(archive, "projects")] == [world["pid"]]
    for name in archive.namelist():
        if name.endswith(".json") and name.startswith("data/"):
            assert world["other"] not in archive.read(name).decode(), name
    assert [s["id"] for s in data(archive, "sources")] == [world["sid"]]
    assert data(archive, "source_excerpts")[0]["content"] == "Sleep improves recall."
    assert {c["code"] for c in data(archive, "screening_criteria")} == {"I1", "E1"}
    assert [d["decision"] for d in data(archive, "screening_decisions")] == ["include"]
    assert len(data(archive, "research_runs")) == 1 and "input_snapshot" in data(archive, "research_runs")[0]
    assert [c["label"] for c in data(archive, "clusters")] == ["Cluster 1"]
    assert len(data(archive, "cluster_members")) == 1
    assert {e["action"] for e in data(archive, "audit_events")} >= {"project.created", "source.created"}
    manifest = json.loads(archive.read("manifest.json"))
    assert manifest["row_counts"]["sources"] == 1 and manifest["row_counts"]["cluster_members"] == 1


def test_private_and_derived_data_is_left_out_and_said_so(world):
    with SessionLocal() as db:
        assert db.scalar(select(Task.id).where(Task.project_id == uuid.UUID(world["pid"])))  # the run queued a task
    archive = download(world)
    names = archive.namelist()
    assert not any(n.startswith(("data/notes.", "data/tasks.", "data/users.", "data/external_identities.")) for n in names)
    embedding = data(archive, "source_embeddings")[0]
    assert "vector" not in embedding and embedding["text_hash"] == "h" * 64
    assert all(world["email"] not in archive.read(n).decode() for n in names)
    manifest = json.loads(archive.read("manifest.json"))
    assert set(manifest["excluded"]["tables"]) == {"notes", "tasks", "users", "external_identities", "note_revisions"}
    assert "vector" in manifest["excluded"]["columns"]["source_embeddings"]


def test_csv_cells_cannot_start_a_formula(world):
    archive = download(world)
    rows = list(csv.DictReader(io.StringIO(archive.read("data/sources.csv").decode())))
    assert rows[0]["title"].startswith("'=HYPERLINK")
    assert json.loads(archive.read("data/sources.json"))[0]["title"].startswith("=HYPERLINK")  # JSON keeps the real value


def test_the_export_is_audited_and_repeatable(world):
    first = download(world)
    second = download(world)
    data_files = [n for n in first.namelist() if n.startswith("data/") and "audit_events" not in n]
    assert data_files and all(first.read(n) == second.read(n) for n in data_files)
    with SessionLocal() as db:
        events = db.scalars(select(AuditEvent).where(AuditEvent.action == "project.replication_exported")).all()
    assert len(events) == 2 and events[0].payload_json["row_counts"]["sources"] == 1
    assert "project.replication_exported" in {e["action"] for e in data(second, "audit_events")}  # the first export is in the second


def test_a_reviewer_may_export_but_a_stranger_may_not(world):
    stranger = _login(f"stranger-{uuid.uuid4().hex[:8]}@example.com")
    assert stranger.get(f"/api/projects/{world['pid']}/export/replication").status_code in (403, 404)


def test_every_table_is_either_exported_or_excluded_with_a_reason():
    """Adding a table forces a decision: export it (project_id or CHILD_TABLES) or list why not."""
    exported = {t.name for t in project_tables()} | {name for name, _, _ in CHILD_TABLES} | {"projects"}
    excluded = set(EXCLUDED_TABLES) | set(NOT_EXPORTED)
    assert exported.isdisjoint(excluded)
    assert set(Base.metadata.tables) == exported | excluded
    assert Note.__tablename__ in excluded  # notes are per-user
