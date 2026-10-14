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
