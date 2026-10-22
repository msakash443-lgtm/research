"""X.145: one log setup for web and worker, and failures that used to vanish are now logged."""

import logging

from fastapi.testclient import TestClient

from app import audit, main, task_runner
from app.agent import executor
from app.config import get_settings
from app.logging_config import LOG_FORMAT, configure_logging


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
