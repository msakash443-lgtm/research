from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import privacy_profile
from app.config import Settings, get_settings
from app.database import SessionLocal
from app.main import app
from app.models import Project, ProjectStage, ResearchRun, ResearchRunStatus, User, utcnow
from app.privacy_profile import (
    PrivacyProfileError,
    available_privacy_profiles,
    effective_privacy,
    load_privacy_profile,
)

PRODUCTION = dict(
    environment="production",
    session_secret="x" * 40,
    database_url="postgresql+psycopg://u:p@db.internal:5432/research?sslmode=require",
    auto_create_schema=False,
    public_origin="https://lab.example.org",
    google_client_id="id",
    google_client_secret="secret",
    google_redirect_uri="https://lab.example.org/api/auth/callback",
    _env_file=None,
)


def _profile_dir(tmp_path, monkeypatch, body: str, name="eu_only"):
    (tmp_path / f"{name}.toml").write_text(
        f'name = "{name}"\nversion = 1\nlabel = "L"\ndescription = "D"\n\n[settings]\n{body}\n', encoding="utf-8"
    )
    monkeypatch.setattr(privacy_profile, "PROFILE_DIR", tmp_path)
    privacy_profile._load.cache_clear()
    yield
    privacy_profile._load.cache_clear()


@pytest.fixture
def eu_only(tmp_path, monkeypatch):
    yield from _profile_dir(tmp_path, monkeypatch, 'allowed_regions = ["eu-west-1", "eu-central-1"]')


def test_shipped_profiles_load_and_choose_no_retention_period():
    names = available_privacy_profiles()
    assert {"default", "gdpr", "dpdp"} <= set(names)
    for name in names:
        profile = load_privacy_profile(name)
        assert profile.name == name
        # Decisions log 2026-10-19: no profile may pick a retention period for the owner.
        assert profile.settings.run_snapshot_retention_days is None


def test_unknown_profile_fails_settings_loudly():
    with pytest.raises(ValueError, match="does not exist"):
        Settings(_env_file=None, privacy_profile="gdrp")


def test_a_profile_with_a_misspelt_setting_is_rejected(tmp_path, monkeypatch):
    gen = _profile_dir(tmp_path, monkeypatch, "retention = 5", name="typo")
    next(gen)
    with pytest.raises(PrivacyProfileError, match="invalid"):
        load_privacy_profile("typo")
    with pytest.raises(ValueError, match="invalid"):
        Settings(_env_file=None, privacy_profile="typo")
    next(gen, None)


def test_explicit_retention_beats_the_profile_default(tmp_path, monkeypatch):
    gen = _profile_dir(tmp_path, monkeypatch, "run_snapshot_retention_days = 90", name="ninety")
    next(gen)
    assert effective_privacy(Settings(_env_file=None, privacy_profile="ninety"))["run_snapshot_retention_days"] == 90
    explicit = Settings(_env_file=None, privacy_profile="ninety", research_run_snapshot_retention_days=30)
    assert effective_privacy(explicit)["run_snapshot_retention_days"] == 30
    next(gen, None)


def test_production_refuses_a_missing_or_disallowed_region(eu_only):
    with pytest.raises(ValueError, match="set DATA_RESIDENCY_REGION"):
        Settings(**PRODUCTION, privacy_profile="eu_only")
    with pytest.raises(ValueError, match="not allowed"):
        Settings(**PRODUCTION, privacy_profile="eu_only", data_residency_region="us-east-1")
    ok = Settings(**PRODUCTION, privacy_profile="eu_only", data_residency_region="eu-west-1")
    assert effective_privacy(ok)["data_residency_region"] == "eu-west-1"


def test_development_does_not_enforce_the_region(eu_only):
    Settings(_env_file=None, privacy_profile="eu_only")


def test_worker_purges_with_the_profile_default(tmp_path, monkeypatch):
    from app import worker

    gen = _profile_dir(tmp_path, monkeypatch, "run_snapshot_retention_days = 30", name="thirty")
    next(gen)
    db = SessionLocal()
    user = User(email=f"privprofile-{utcnow().timestamp()}@example.com")
    db.add(user)
    db.flush()
    project = Project(owner_id=user.id, title="P", description="d", stage=ProjectStage.idea)
    db.add(project)
    db.flush()
    run = ResearchRun(
        project_id=project.id, question="q", status=ResearchRunStatus.completed,
        input_snapshot={"evidence": "old"}, completed_at=utcnow() - timedelta(days=60),
    )
    db.add(run)
    db.commit()
    run_id = run.id
    db.close()

    settings = get_settings()
    monkeypatch.setattr(settings, "research_run_snapshot_retention_days", None)
    monkeypatch.setattr(settings, "privacy_profile", "thirty")
    assert worker.sweep_run_snapshot_retention() >= 1

    db = SessionLocal()
    assert db.get(ResearchRun, run_id).input_snapshot is None
    db.close()
    next(gen, None)


def test_profile_endpoint_needs_sign_in_and_reports_the_profile():
    client = TestClient(app)
    assert client.get("/api/privacy/profile").status_code == 401

    client.post("/api/auth/development/login", json={"email": "privprofile@example.com", "display_name": "P"})
    response = client.get("/api/privacy/profile")

    assert response.status_code == 200
    body = response.json()
    assert body["profile"] == get_settings().privacy_profile
    assert body["profile_version"] >= 1
    assert "allowed_regions" in body
