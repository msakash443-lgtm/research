"""Acceptance suite for spec `docs/research-automation-spec.md` §13 ("Acceptance Criteria (end-to-end)")
(plan task X.10). One test per numbered criterion, each driving the real FastAPI app (TestClient) with the
SQLite test fixtures from `conftest.py`; no network (`fake_llm` fixture / in-process object-store fakes only).

Mapping (criterion -> test):
    13.1 -> test_13_1_idea_to_verified_evidence_table_and_ranked_gap_list_with_search_log_and_prisma_counts
    13.2 -> test_13_2_citations_resolve_against_verified_sources_and_unverifiable_ones_block_export
    13.3 -> test_13_3_every_manuscript_number_traces_to_an_analysis_run_with_script_hash_and_environment
    13.4 -> test_13_4_an_ai_generated_artifact_shows_who_what_when_which_model_and_its_approval_status
    13.5 -> test_13_5_re_entering_an_earlier_stage_marks_downstream_artifacts_stale_with_a_rerun_path
    13.6 -> test_13_6_a_full_audit_replication_package_exports_in_one_action
"""

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models import (
    AnalysisPlan,
    AnalysisRun,
    Artifact,
    ArtifactStatus,
    AuditEvent,
    DatasetVersion,
    Document,
    DocumentVersion,
    Extraction,
    Project,
    ProjectStage,
    SearchQuery,
    Source,
    Task,
    TaskStatus,
)
from app.object_storage import fulltext_key
from app.task_runner import run_one_task
from tests.test_fulltext import make_pdf


def _login(email: str) -> TestClient:
    client = TestClient(app, base_url="http://testserver")
    client.post("/api/auth/development/login", json={"email": email, "display_name": "Acceptance"})
    return client


def _login_with_id(email: str):
    client = TestClient(app, base_url="http://testserver")
    user_id = client.post("/api/auth/development/login", json={"email": email, "display_name": "Acceptance"}).json()["id"]
    return client, user_id


def _tag() -> str:
    return uuid.uuid4().hex[:8]


# ---- shared ProseMirror-content builders (same shape as test_manuscript_export.py) -----------------------

def _prov(sid, user_id):
    return {"type": "prov", "attrs": {"sid": sid, "origin": "typed", "by": user_id, "sug": None, "note": None, "paste": None, "src": None}}


def _text(text, sid, user_id):
    return {"type": "text", "text": text, "marks": [_prov(sid, user_id)]}


def _cite(source_id):
    return {"type": "cite", "attrs": {"source_id": source_id}}


def _stat(run_id, path):
    return {"type": "stat", "attrs": {"analysis_run_id": run_id, "path": path}}


def _new_document(project_id, user_id, title, position):
    with SessionLocal() as db:
        document = Document(project_id=uuid.UUID(project_id), title=title, position=position, created_by=f"user:{user_id}")
        db.add(document)
        db.commit()
        return str(document.id)


def _save(client, project_id, document_id, content):
    response = client.post(
        f"/api/projects/{project_id}/documents/{document_id}/versions",
        json={"base_number": 0, "client_save_id": uuid.uuid4().hex, "client_session_id": "s", "content_json": content},
    )
    assert response.status_code == 201, response.text


def _write_version_directly(project_id, document_id, user_id, content, client_save_id):
    """Writes a `DocumentVersion` row directly, bypassing the save API's own guard — the same pattern
    `test_manuscript_export.py` uses to prove the *export* guard independently of the save-time guard."""
    with SessionLocal() as db:
        db.add(DocumentVersion(
            document_id=uuid.UUID(document_id), project_id=uuid.UUID(project_id), number=1, content_json=content,
            content_text="", content_hash="0" * 64, saved_by=user_id, client_save_id=client_save_id,
        ))
        db.commit()


def _source(project_id, **fields):
    with SessionLocal() as db:
        row = Source(project_id=uuid.UUID(project_id), **fields)
        db.add(row)
        db.commit()
        return str(row.id)


def _export(client, project_id, fmt="md"):
    return client.get(f"/api/projects/{project_id}/manuscript/export?format={fmt}")


# =============================================================================================================
# 13.1 "A scholar can go from a rough idea to a verified evidence table and ranked gap list with a complete
#       search log and PRISMA counts."
# =============================================================================================================

def test_13_1_idea_to_verified_evidence_table_and_ranked_gap_list_with_search_log_and_prisma_counts(monkeypatch):
    from app.routers import extractions as extraction_routes

    tag = _tag()
    client = _login(f"acc131-{tag}@example.com")
    pid = client.post("/api/projects", json={"title": "Acceptance 13.1"}).json()["id"]

    # Idea/scoping artifact: inclusion/exclusion criteria.
    criteria_resp = client.put(f"/api/projects/{pid}/criteria", json={
        "framework": "custom", "criteria": [
            {"kind": "include", "text": "Empirical studies"},
            {"kind": "exclude", "text": "Opinion pieces"},
        ],
    })
    assert criteria_resp.status_code == 200, criteria_resp.text

    # Complete, versioned search log (spec 5.2.5 / 4 SearchQuery).
    with SessionLocal() as db:
        db.add(SearchQuery(
            project_id=uuid.UUID(pid), search_id=uuid.uuid4(), database="openalex",
            query_string="remote work AND productivity", run_at=datetime.now(timezone.utc),
            n_results=3, version=1, counts={"retrieved": 3},
            results=[{"id": "w:1", "doi": None, "work_key": "k1"},
                     {"id": "w:2", "doi": None, "work_key": "k2"},
                     {"id": "w:3", "doi": None, "work_key": "k3"}],
            created_by="test-setup",
        ))
        db.commit()

    a = client.post(f"/api/projects/{pid}/sources", json={"title": "Paper A"}).json()["id"]
    b = client.post(f"/api/projects/{pid}/sources", json={"title": "Paper B"}).json()["id"]
    c = client.post(f"/api/projects/{pid}/sources", json={"title": "Paper C"}).json()["id"]

    assert client.post(f"/api/projects/{pid}/screening/decisions", json={"source_id": a, "decision": "include"}).status_code == 201
    assert client.post(f"/api/projects/{pid}/screening/decisions", json={"source_id": b, "decision": "include"}).status_code == 201
    assert client.post(f"/api/projects/{pid}/screening/decisions",
                       json={"source_id": c, "decision": "exclude", "reason_code": "E1"}).status_code == 201

    prisma = client.get(f"/api/projects/{pid}/prisma").json()
    assert prisma["identification"]["identified"] == 3
    assert len(prisma["identification"]["searches"]) == 1
    assert prisma["screening"]["title_abstract"] == {
        "decided": 3, "included": 2, "excluded": 1, "conflicts": 0, "excluded_by_reason": {"E1": 1},
    }
    assert prisma["screening"]["exclusion_reasons_complete"] is True

    # Verified evidence table: a human extraction, checked against the stored PDF, then confirmed.
    pdf = make_pdf([["Introduction", "We ask whether remote work raises output",
                     "Method", "120 clerks in the Netherlands", "a randomised trial"]])
    key = fulltext_key(uuid.UUID(pid), uuid.UUID(a), "pdf")

    class Store:
        def get(self, requested_key):
            assert requested_key == key
            return pdf

    monkeypatch.setattr(extraction_routes, "get_object_store", lambda settings: Store())
    with SessionLocal() as db:
        db.get(Source, uuid.UUID(a)).fulltext_path = key
        db.commit()

    created = client.post(f"/api/projects/{pid}/extractions", json={
        "source_id": a,
        "fields": {
            "research_question": "Does remote work raise output?",
            "sample": {"n": 120, "population": "clerks", "country": "NL"},
        },
        "evidence": {
            "research_question": {"quote": "We ask whether remote work raises output", "page": 1, "section": "Introduction"},
            "sample": {"quote": "120 clerks in the Netherlands", "page": 1, "section": "Method"},
        },
    })
    assert created.status_code == 201, created.text
    extraction_id = created.json()["id"]
    verified = client.post(f"/api/projects/{pid}/extractions/{extraction_id}/verify",
                           json={"confirmed_critical_fields": ["measures", "sample", "key_findings"],
                                 "confirmed_not_reported_fields": ["measures", "key_findings"]})
    assert verified.status_code == 200, verified.text
    assert verified.json()["verified_by_human"] is True

    matrix = client.get(f"/api/projects/{pid}/evidence-matrix").json()
    paper_a = next(p for p in matrix["papers"] if p["source_id"] == a)
    assert paper_a["verified_by_human"] is True
    assert paper_a["cells"]["sample"]["status"] == "checked"

    # Ranked gap list, each with >= 2 supporting papers.
    low = client.post(f"/api/projects/{pid}/gaps", json={
        "type": "empirical", "statement": "Low priority gap", "supporting_paper_ids": [a, b], "priority": 2,
    })
    high = client.post(f"/api/projects/{pid}/gaps", json={
        "type": "empirical", "statement": "High priority gap", "supporting_paper_ids": [a, b], "priority": 9,
    })
    assert low.status_code == 201 and high.status_code == 201

    gaps = client.get(f"/api/projects/{pid}/gaps").json()
    assert [g["statement"] for g in gaps] == ["High priority gap", "Low priority gap"]


# =============================================================================================================
# 13.2 "Every citation in any generated text resolves and matches its source; unverifiable references cannot
#       be exported."
# =============================================================================================================

def test_13_2_citations_resolve_against_verified_sources_and_unverifiable_ones_block_export():
    client, user_id = _login_with_id(f"acc132-{_tag()}@example.com")
    pid = client.post("/api/projects", json={"title": "Acceptance 13.2"}).json()["id"]

    verified = _source(pid, title="Wages after remote work", authors=["Smith, Jane"], year=2021,
                       venue="Labour Journal", doi="10.1/abc", metadata_verified=True)
    unverified = _source(pid, title="Unchecked claim", authors=["Doe, Joe"], year=2020, metadata_verified=False)

    good = _new_document(pid, user_id, "Introduction", 1)
    _save(client, pid, good, {"type": "doc", "content": [
        {"type": "paragraph", "content": [_text("Wages rose ", "a", user_id), _cite(verified)]},
    ]})

    clean_export = _export(client, pid, "md")
    assert clean_export.status_code == 200
    # the citation resolves and matches its source: the reference appears, correctly attributed.
    assert "Smith" in clean_export.text.split("## References")[1]

    bad = _new_document(pid, user_id, "Discussion", 2)
    _write_version_directly(pid, bad, user_id, {"type": "doc", "content": [
        {"type": "paragraph", "content": [_text("See ", "b", user_id), _cite(unverified)]},
    ]}, client_save_id="unver")

    blocked = _export(client, pid, "md")
    assert blocked.status_code == 409
    detail = blocked.json()["detail"]
    cited = detail["citations"][0]
    assert cited["document_id"] == bad and cited["kind"] == "unverified_citation" and cited["subject"] == unverified


# =============================================================================================================
# 13.3 "Every number in the manuscript is traceable to an analysis run with a stored script, data hash, and
#       environment."
# =============================================================================================================

def _analysis_run(project_id, output, user_id):
    with SessionLocal() as db:
        dataset = DatasetVersion(
            project_id=uuid.UUID(project_id), version_no=1, kind="raw", name="d.csv", content="a\n1\n",
            sha256="0" * 64, row_count=1, column_names=["a"], created_by=user_id,
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


def test_13_3_every_manuscript_number_traces_to_an_analysis_run_with_script_hash_and_environment():
    client, user_id = _login_with_id(f"acc133-{_tag()}@example.com")
    pid = client.post("/api/projects", json={"title": "Acceptance 13.3"}).json()["id"]
    run_id = _analysis_run(pid, {"descriptives": {"age": {"mean": 35.5}}}, user_id)

    with SessionLocal() as db:
        run = db.get(AnalysisRun, uuid.UUID(run_id))
        # the record itself names the script, dataset and environment hashes it ran with.
        assert run.script_sha256 == "3" * 64
        assert run.dataset_sha256 == "0" * 64
        assert run.environment_sha256 == "2" * 64

    good = _new_document(pid, user_id, "Results", 1)
    _save(client, pid, good, {"type": "doc", "content": [
        {"type": "paragraph", "content": [_text("Mean age was ", "x", user_id), _stat(run_id, "descriptives.age.mean")]},
    ]})
    resolved_export = _export(client, pid, "md")
    assert resolved_export.status_code == 200
    assert "Mean age was 35.5" in resolved_export.text

    bad = _new_document(pid, user_id, "Discussion", 2)
    _write_version_directly(pid, bad, user_id, {"type": "doc", "content": [
        {"type": "paragraph", "content": [_stat(run_id, "descriptives.age.missing")]},
    ]}, client_save_id="unresolved")

    blocked_export = _export(client, pid, "md")
    assert blocked_export.status_code == 409
    detail = blocked_export.json()["detail"]
    assert detail["citations"][0]["kind"] == "unresolved_number"
    assert detail["citations"][0]["subject"] == f"{run_id}:descriptives.age.missing"


# =============================================================================================================
# 13.4 "Every AI-generated artifact shows who/what/when/which model and its human approval status."
# =============================================================================================================

def test_13_4_an_ai_generated_artifact_shows_who_what_when_which_model_and_its_approval_status(fake_llm, monkeypatch):
    import json as jsonlib

    from app import task_handlers
    from app.models import ProjectExtractionSchema
    from app.routers import extractions as extraction_routes

    fake_llm.handler = lambda request: {"choices": [{"message": {"content": jsonlib.dumps({
        "research_question": {
            "value": "Does remote work raise output?",
            "evidence": {"quote": "We study remote work and output.", "page": 1, "section": "Abstract"},
            "confidence": 0.9,
        },
    })}}]}

    client, user_id = _login_with_id(f"acc134-{_tag()}@example.com")
    pid = client.post("/api/projects", json={"title": "Acceptance 13.4"}).json()["id"]
    source_id = client.post(f"/api/projects/{pid}/sources", json={"title": "A paper"}).json()["id"]
    pdf = make_pdf([["Abstract", "We study remote work and output."]])
    key = fulltext_key(uuid.UUID(pid), uuid.UUID(source_id), "pdf")

    class Store:
        def get(self, requested_key):
            return pdf

    monkeypatch.setattr(task_handlers, "get_object_store", lambda settings: Store())
    monkeypatch.setattr(extraction_routes, "get_object_store", lambda settings: Store())
    with SessionLocal() as db:
        db.get(Source, uuid.UUID(source_id)).fulltext_path = key
        db.add(ProjectExtractionSchema(
            project_id=uuid.UUID(pid), name="brief", version=1, label="Brief", description="",
            fields=[{"key": "research_question", "type": "string", "description": "The stated question.", "critical": False}],
            based_on="default@1", created_by=user_id,
        ))
        db.commit()

    queued = client.post(f"/api/projects/{pid}/extractions/ai", json={"source_id": source_id, "schema_name": "project:brief"})
    assert queued.status_code == 202, queued.text
    assert run_one_task(task_id=uuid.UUID(queued.json()["task_id"]))

    with SessionLocal() as db:
        task = db.get(Task, uuid.UUID(queued.json()["task_id"]))
        assert task.status == TaskStatus.completed
        extraction = db.scalar(select(Extraction).where(Extraction.task_id == task.id))
        extraction_id = str(extraction.id)

    before = client.get(f"/api/projects/{pid}/extractions/{extraction_id}").json()
    # "what/which model": AI-produced, naming the exact model and prompt version.
    assert before["extracted_by"] == "ai"
    assert before["model_id"] == "fake-model"
    assert before["prompt_version"] == "evidence_extraction@1"
    assert before["extractor"].startswith("agent:")
    # "when": a creation timestamp.
    assert before["created_at"]
    # "approval status": unapproved until a human confirms.
    assert before["verified_by_human"] is False
    assert before["verified_by"] is None and before["verified_at"] is None

    verified = client.post(f"/api/projects/{pid}/extractions/{extraction_id}/verify",
                           json={"confirmed_critical_fields": []})
    assert verified.status_code == 200, verified.text
    after = verified.json()
    # "who" approved it, and "when".
    assert after["verified_by_human"] is True
    assert after["verified_by"] is not None
    assert after["verified_at"] is not None


# =============================================================================================================
# 13.5 "The scholar can re-enter any stage and downstream artifacts are flagged as stale with a clear re-run
#       path."
# =============================================================================================================

def test_13_5_re_entering_an_earlier_stage_marks_downstream_artifacts_stale_with_a_rerun_path():
    client = _login(f"acc135-{_tag()}@example.com")
    pid = client.post("/api/projects", json={"title": "Acceptance 13.5"}).json()["id"]

    criteria_resp = client.put(f"/api/projects/{pid}/criteria", json={
        "framework": "custom", "criteria": [{"kind": "include", "text": "Empirical studies"}],
    })
    assert criteria_resp.status_code == 200

    # Simulate having already advanced well past criteria (`criteria` is an artifact at `search_planned`).
    with SessionLocal() as db:
        db.get(Project, uuid.UUID(pid)).stage = ProjectStage.extracted
        db.commit()

    reentry = client.post(f"/api/projects/{pid}/stage/reenter", json={"stage": "scoped", "reason": "found a flaw in scoping"})
    assert reentry.status_code == 200, reentry.text
    assert reentry.json()["from_stage"] == "extracted" and reentry.json()["to_stage"] == "scoped"

    stale = client.get(f"/api/projects/{pid}/artifacts", params={"status": "stale"}).json()
    assert any(row["kind"] == "criteria" for row in stale)

    with SessionLocal() as db:
        criteria_artifact = db.scalar(select(Artifact).where(Artifact.project_id == uuid.UUID(pid), Artifact.kind == "criteria"))
        assert criteria_artifact.status == ArtifactStatus.stale

    rerun_path = client.get(f"/api/projects/{pid}/rerun-path").json()
    # a clear, ordered path back through the work that must be redone.
    assert rerun_path, "re-entry must leave a concrete re-run path"
    assert rerun_path[0]["stage"] == "search_planned" and rerun_path[0]["gate"] == "G2"


# =============================================================================================================
# 13.6 "A full audit/replication package can be exported in one action."
# =============================================================================================================

def test_13_6_a_full_audit_replication_package_exports_in_one_action():
    import io
    import json as jsonlib
    import zipfile

    client = _login(f"acc136-{_tag()}@example.com")
    pid = client.post("/api/projects", json={"title": "Acceptance 13.6"}).json()["id"]
    client.post(f"/api/projects/{pid}/sources", json={"title": "A paper"})

    response = client.get(f"/api/projects/{pid}/export/replication")
    assert response.status_code == 200, response.text
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = set(archive.namelist())

    manifest = jsonlib.loads(archive.read("manifest.json"))
    assert "README.md" in names
    assert "data/sources.json" in names
    assert "data/audit_events.json" in names  # the full audit log is in the one package
    # the manifest checksums every file in the archive (sha256 per file), one action, repeatable.
    import hashlib as _hashlib
    for name in names - {"manifest.json"}:
        assert manifest["files"][name]["sha256"] == _hashlib.sha256(archive.read(name)).hexdigest()

    with SessionLocal() as db:
        exported = db.scalars(select(AuditEvent).where(
            AuditEvent.project_id == uuid.UUID(pid), AuditEvent.action == "project.replication_exported",
        )).all()
    assert len(exported) == 1  # the export itself is audited, inside the same package it produced
