import uuid
from datetime import timedelta

from app.config import get_settings
from app.database import SessionLocal
from app.models import Project, ProjectStage, ResearchRun, ResearchRunStatus, User, utcnow
from app.retention import purge_expired_run_snapshots, snapshot_cutoff


def _make_project(db):
    user = User(email=f"retention-{uuid.uuid4().hex}@example.com")
    db.add(user)
    db.flush()
    project = Project(owner_id=user.id, title="Retention test", description="d", stage=ProjectStage.idea)
    db.add(project)
    db.flush()
    return project


def _make_run(db, project, *, status, completed_at, snapshot):
    run = ResearchRun(
        project_id=project.id,
        question="q",
        status=status,
        input_snapshot=snapshot,
        completed_at=completed_at,
    )
    db.add(run)
    db.flush()
    return run


def test_snapshot_cutoff_is_none_when_retention_is_unset():
    assert snapshot_cutoff(utcnow(), None) is None


def test_snapshot_cutoff_is_now_minus_the_configured_days():
    now = utcnow()
    assert snapshot_cutoff(now, 30) == now - timedelta(days=30)


def test_purge_is_a_noop_when_no_retention_period_is_configured():
    db = SessionLocal()
    project = _make_project(db)
    old_run = _make_run(
        db, project, status=ResearchRunStatus.completed,
        completed_at=utcnow() - timedelta(days=400), snapshot={"evidence": "secret"},
    )
    db.commit()
    cleared = purge_expired_run_snapshots(db, utcnow(), None)
    db.commit()
    assert cleared == 0
    db.refresh(old_run)
    assert old_run.input_snapshot == {"evidence": "secret"}
    db.close()


def test_purge_clears_only_finished_runs_past_the_cutoff():
    db = SessionLocal()
    project = _make_project(db)
    now = utcnow()
    old_completed = _make_run(
        db, project, status=ResearchRunStatus.completed,
        completed_at=now - timedelta(days=400), snapshot={"evidence": "old"},
    )
    old_failed = _make_run(
        db, project, status=ResearchRunStatus.failed,
        completed_at=now - timedelta(days=400), snapshot={"evidence": "old-failed"},
    )
    recent = _make_run(
        db, project, status=ResearchRunStatus.completed,
        completed_at=now - timedelta(days=10), snapshot={"evidence": "recent"},
    )
    still_running = _make_run(
        db, project, status=ResearchRunStatus.running, completed_at=None, snapshot={"evidence": "live"},
    )
    db.commit()

    cleared = purge_expired_run_snapshots(db, now, 365)
    db.commit()

    assert cleared == 2
    for run in (old_completed, old_failed):
        db.refresh(run)
        assert run.input_snapshot is None
    for run in (recent, still_running):
        db.refresh(run)
        assert run.input_snapshot is not None
    db.close()


def test_purge_leaves_the_answer_status_and_timestamps_untouched():
    db = SessionLocal()
    project = _make_project(db)
    run = _make_run(
        db, project, status=ResearchRunStatus.completed,
        completed_at=utcnow() - timedelta(days=400), snapshot={"evidence": "old"},
    )
    run.answer = "the synthesized answer"
    db.commit()
    completed_at = run.completed_at.replace(tzinfo=None)

    purge_expired_run_snapshots(db, utcnow(), 30)
    db.commit()
    db.refresh(run)

    assert run.input_snapshot is None
    assert run.answer == "the synthesized answer"
    assert run.status == ResearchRunStatus.completed
    assert run.completed_at.replace(tzinfo=None) == completed_at
    db.close()


def test_worker_sweep_is_a_noop_until_the_setting_is_configured(monkeypatch):
    from app import worker

    db = SessionLocal()
    project = _make_project(db)
    run = _make_run(
        db, project, status=ResearchRunStatus.completed,
        completed_at=utcnow() - timedelta(days=400), snapshot={"evidence": "old"},
    )
    run_id = run.id
    db.commit()
    db.close()

    monkeypatch.setattr(get_settings(), "research_run_snapshot_retention_days", None)
    assert worker.sweep_run_snapshot_retention() == 0

    monkeypatch.setattr(get_settings(), "research_run_snapshot_retention_days", 365)
    assert worker.sweep_run_snapshot_retention() == 1

    db = SessionLocal()
    assert db.get(ResearchRun, run_id).input_snapshot is None
    db.close()
