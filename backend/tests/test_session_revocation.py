from fastapi.testclient import TestClient

from app.config import Settings
from app.database import SessionLocal
from app.main import app
from app.models import User


def _signed_in(email):
    client = TestClient(app)
    response = client.post("/api/auth/development/login", json={"email": email, "display_name": "R"})
    assert response.status_code == 200
    return client


def test_sign_out_everywhere_ends_sessions_on_other_devices():
    laptop = _signed_in("revoke@example.com")
    phone = _signed_in("revoke@example.com")
    assert laptop.get("/api/auth/me").status_code == 200
    assert phone.get("/api/auth/me").status_code == 200

    assert laptop.post("/api/auth/logout-everywhere").status_code == 204

    assert phone.get("/api/auth/me").status_code == 401
    assert laptop.get("/api/auth/me").status_code == 401


def test_sign_out_everywhere_does_not_affect_other_users():
    alice = _signed_in("alice-revoke@example.com")
    bob = _signed_in("bob-revoke@example.com")

    assert alice.post("/api/auth/logout-everywhere").status_code == 204

    assert bob.get("/api/auth/me").status_code == 200


def test_new_sign_in_after_sign_out_everywhere_works():
    client = _signed_in("again@example.com")
    client.post("/api/auth/logout-everywhere")

    fresh = _signed_in("again@example.com")

    assert fresh.get("/api/auth/me").status_code == 200


def test_new_users_start_at_epoch_zero():
    # Cookies issued before this change carry no epoch and are treated as epoch 0, so they stay valid.
    _signed_in("legacy@example.com")
    db = SessionLocal()
    user = db.query(User).filter(User.email == "legacy@example.com").one()
    assert user.session_epoch == 0
    db.close()


def test_session_lifetime_is_configurable_with_fourteen_day_default():
    assert Settings(_env_file=None).session_max_age_seconds == 14 * 24 * 60 * 60


def test_a_session_older_than_the_max_age_is_refused_even_while_in_use(monkeypatch):
    import time
    from types import SimpleNamespace

    from app import dependencies
    from app.config import get_settings

    client = _signed_in("absolute-lifetime@example.com")
    assert client.get("/api/auth/me").status_code == 200
    later = time.time() + get_settings().session_max_age_seconds + 1
    monkeypatch.setattr(dependencies, "time", SimpleNamespace(time=lambda: later))

    assert client.get("/api/auth/me").status_code == 401


def test_logout_everywhere_is_audited():
    from sqlalchemy import select

    from app.models import AuditEvent

    client = _signed_in("audited-signout@example.com")
    user_id = client.get("/api/auth/me").json()["id"]

    assert client.post("/api/auth/logout-everywhere").status_code == 204

    with SessionLocal() as db:
        actions = db.scalars(select(AuditEvent.action).where(AuditEvent.actor == user_id)).all()
    assert "user.signed_out_everywhere" in actions
