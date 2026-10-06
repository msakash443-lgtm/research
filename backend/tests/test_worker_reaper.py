import uuid
from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.database import SessionLocal
from app.models import Project, ResearchRun, ResearchRunStatus, User
from app.worker import claim_next_run, reap_stale_runs


def _make_run(status, started_at, attempt_count):
    with SessionLocal() as db:
        user = User(email=f"reaper-{uuid.uuid4().hex}@example.test")
        db.add(user)
        db.flush()
        project = Project(owner_id=user.id, title="Reaper")
        db.add(project)
        db.flush()
        run = ResearchRun(
            project_id=project.id,
            question="q",
            status=status,
            started_at=started_at,
            attempt_count=attempt_count,
        )
        db.add(run)
        db.commit()
        return run.id


def _get(run_id):
    with SessionLocal() as db:
        run = db.get(ResearchRun, run_id)
        db.expunge(run)
        return run


def test_stale_running_run_is_requeued_and_can_be_claimed_again():
    lease = get_settings().research_run_lease_seconds
    old = datetime.now(timezone.utc) - timedelta(seconds=lease + 60)
    run_id = _make_run(ResearchRunStatus.running, old, attempt_count=1)

    assert reap_stale_runs() == 1

    run = _get(run_id)
    assert run.status == ResearchRunStatus.queued
    assert run.started_at is None
    assert claim_next_run() == str(run_id)


def test_run_at_max_attempts_is_failed_loudly():
    settings = get_settings()
    old = datetime.now(timezone.utc) - timedelta(seconds=settings.research_run_lease_seconds + 60)
    run_id = _make_run(ResearchRunStatus.running, old, attempt_count=settings.research_run_max_attempts)

    assert reap_stale_runs() == 1

    run = _get(run_id)
    assert run.status == ResearchRunStatus.failed
    assert "abandoned" in run.error_message
    assert run.completed_at is not None
    assert run.answer is None


def test_fresh_running_and_queued_runs_are_untouched():
    fresh = datetime.now(timezone.utc) - timedelta(seconds=5)
    running_id = _make_run(ResearchRunStatus.running, fresh, attempt_count=1)
    queued_id = _make_run(ResearchRunStatus.queued, None, attempt_count=0)

    assert reap_stale_runs() == 0

    assert _get(running_id).status == ResearchRunStatus.running
    assert _get(queued_id).status == ResearchRunStatus.queued
