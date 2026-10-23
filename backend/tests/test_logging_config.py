"""X.145: one log setup for web and worker, and failures that used to vanish are now logged."""

import logging
import uuid

from fastapi.testclient import TestClient

from app import audit, erasure, main, task_runner
from app.agent import executor
from app.config import get_settings
from app.database import SessionLocal
from app.logging_config import LOG_FORMAT, configure_logging
from app.models import Note, NoteAttachment, NoteKind, NoteStatus, utcnow
from app.object_storage import ObjectStorageError


def _errors(caplog, text):
    return [r for r in caplog.records if r.levelno == logging.ERROR and text in r.getMessage()]


def test_configure_logging_gives_the_root_logger_one_stderr_handler_at_info(monkeypatch):
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", [])
    monkeypatch.setattr(root, "level", logging.WARNING)

    configure_logging()
    configure_logging()  # a second call adds nothing

    assert len(root.handlers) == 1
    assert root.level == logging.INFO
    assert root.handlers[0].formatter._fmt == LOG_FORMAT


def test_a_failed_readiness_check_logs_why_and_still_answers_503(monkeypatch, caplog):
    class DownEngine:
        def connect(self):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(main, "engine", DownEngine())

    response = TestClient(main.app).get("/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not ready", "database": "unavailable"}
    [record] = _errors(caplog, "Readiness check: database unavailable")
    assert record.exc_info and "connection refused" in str(record.exc_info[1])


def test_an_error_while_recording_a_run_failure_is_logged_not_swallowed(monkeypatch, fake_llm, caplog):
    monkeypatch.setattr(get_settings(), "run_research_inline", False)
    client = TestClient(main.app)
    client.post("/api/auth/development/login", json={"email": "log-run@example.com", "display_name": "L"})
    project_id = client.post("/api/projects", json={"title": "Logging"}).json()["id"]
    source_id = client.post(f"/api/projects/{project_id}/sources", json={"title": "S", "evidence_excerpt": "Rose."}).json()["id"]
    client.post(f"/api/projects/{project_id}/sources/{source_id}/verify")
    assert client.post(f"/api/projects/{project_id}/research-runs", json={"question": "A long enough question?"}).status_code == 202

    def explode(*args, **kwargs):
        raise RuntimeError("boom inside the executor")

    real_record = audit.record

    def record(db, *, action, **kwargs):
        if action == "research_run.attempt_failed":
            raise RuntimeError("audit write failed")
        return real_record(db, action=action, **kwargs)

    monkeypatch.setattr(executor, "build_plan", explode)
    monkeypatch.setattr(audit, "record", record)

    assert task_runner.run_one_task() is True

    [record_failure] = _errors(caplog, "Could not record the failure of research run")
    assert "audit write failed" in str(record_failure.exc_info[1])
    # The original error is still the one the task runner reports.
    assert _errors(caplog, "failed on attempt 1")


def test_a_storage_failure_while_erasing_an_unfiled_note_attachment_is_logged_and_the_reference_still_drops(monkeypatch, caplog):
    client = TestClient(main.app)
    client.post("/api/auth/development/login", json={"email": "log-erase@example.com", "display_name": "L"})
    owner_id = uuid.UUID(client.get("/api/auth/me").json()["id"])
    with SessionLocal() as db:
        note = Note(owner_id=owner_id, project_id=None, client_id="log-erase", kind=NoteKind.typed,
                    body="private text", status=NoteStatus.inbox, captured_at=utcnow())
        db.add(note)
        db.flush()
        attachment = NoteAttachment(note_id=note.id, path=f"notes/{owner_id}/{note.id}/photo.png",
                                    media_type="image/png", size=3, sha256="0" * 64)
        db.add(attachment)
        db.commit()
        note_id, attachment_id = note.id, attachment.id

    class StorageDown:
        def delete(self, key):
            raise ObjectStorageError("S3 DELETE failed: 503")

    monkeypatch.setattr(erasure, "get_object_store", lambda settings: StorageDown())

    response = client.post("/api/auth/erase-account", json={"confirm_email": "log-erase@example.com"})

    assert response.status_code == 204
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING and "during account erasure" in r.getMessage()]
    assert warnings == [f"Could not delete stored object for note attachment {attachment_id} during account erasure"]
    with SessionLocal() as db:
        assert db.get(NoteAttachment, attachment_id) is None
        assert db.get(Note, note_id).body == ""
