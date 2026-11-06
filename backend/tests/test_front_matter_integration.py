import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app import front_matter_task
from app.database import SessionLocal
from app.models import AuditEvent, DocumentVersion, ManuscriptOutput
from app.task_registry import PermanentTaskError
from tests.test_manuscript_export import _login, _new_document, _save, _text


def _world():
    client, user_id = _login(f"front-integration-{uuid.uuid4().hex}@example.org")
    pid = client.post("/api/projects", json={"title": "Front integration"}).json()["id"]
    doc = _new_document(pid, user_id, "Introduction", 1)
    _save(client, pid, doc, {"type": "doc", "content": [{"type": "paragraph", "content": [_text("A saved section.", "a", user_id)]}]})
    with SessionLocal() as db:
        version = db.scalar(select(DocumentVersion).where(
            DocumentVersion.document_id == uuid.UUID(doc)
        ).order_by(DocumentVersion.number.desc()))
        return client, pid, doc, str(version.id), user_id

def _task(pid, doc, version):
    return SimpleNamespace(
        payload={"project_id": pid, "document_id": doc, "source_version_id": version, "kind": "abstract", "actor": "user:test"},
        ensure_owned=lambda db=None: None,
    )


def test_a_queued_request_pinned_to_an_old_version_fails_loudly_without_storing_output(monkeypatch):
    client, pid, doc, version, user_id = _world()
    with SessionLocal() as db:
        from app.models import DocumentVersion
        old = db.get(DocumentVersion, uuid.UUID(version))
        old_content = old.content_json
    response = client.post(
        f"/api/projects/{pid}/documents/{doc}/versions",
        json={"base_number": 1, "client_save_id": uuid.uuid4().hex, "client_session_id": "s",
              "content_json": {"type": "doc", "content": [{"type": "paragraph", "content": [_text("Changed after request.", "b", user_id)]}]}},
    )
    assert response.status_code == 201, response.text
    with pytest.raises(PermanentTaskError, match="changed after"):
        front_matter_task.run_front_matter(_task(pid, doc, version), _task(pid, doc, version).payload)
    with SessionLocal() as db:
        assert db.scalars(select(ManuscriptOutput).where(ManuscriptOutput.document_id == uuid.UUID(doc))).all() == []


def test_a_model_failure_is_audited_and_never_writes_output(monkeypatch):
    from app.agent.llm import LLMConfigurationError
    monkeypatch.setattr(front_matter_task, "run", lambda *args: (_ for _ in ()).throw(LLMConfigurationError("not configured")))
    client, pid, doc, version, user_id = _world()
    with pytest.raises(PermanentTaskError):
        front_matter_task.run_front_matter(_task(pid, doc, version), _task(pid, doc, version).payload)
    with SessionLocal() as db:
        assert db.scalars(select(ManuscriptOutput).where(ManuscriptOutput.document_id == uuid.UUID(doc))).all() == []
        assert db.scalar(select(AuditEvent.id).where(AuditEvent.project_id == uuid.UUID(pid), AuditEvent.action == "ai.failed"))
