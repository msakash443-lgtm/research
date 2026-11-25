from app.database import SessionLocal
from app.models import ExternalIdentity, User
from app.routers.auth import user_from_google_claims


def test_google_identity_creates_and_reuses_one_user():
    db = SessionLocal()
    claims = {"sub": "google-123", "email": "Researcher@Example.com", "email_verified": True, "name": "Researcher"}

    first = user_from_google_claims(claims, db)
    second = user_from_google_claims(claims, db)

    assert first.id == second.id
    assert first.email == "researcher@example.com"
    assert db.query(User).count() == 1
    assert db.query(ExternalIdentity).count() == 1
    db.close()


def _fake_google(monkeypatch, *, token=None, error=None):
    import app.routers.auth as auth_module

    class Client:
        async def authorize_access_token(self, request):
            if error:
                raise error
            return token

    monkeypatch.setattr(auth_module, "google_client", lambda: Client())


def test_an_oauth_error_on_callback_is_a_logged_401_not_a_500(monkeypatch, caplog):
    from authlib.integrations.base_client import OAuthError
    from fastapi.testclient import TestClient

    from app.main import app

    _fake_google(monkeypatch, error=OAuthError(error="access_denied"))

    with caplog.at_level("WARNING"):
        response = TestClient(app).get("/api/auth/callback", follow_redirects=False)

    assert response.status_code == 401
    assert "access_denied" in caplog.text


def test_a_callback_without_id_token_claims_is_refused(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    _fake_google(monkeypatch, token={"access_token": "x"})
    client = TestClient(app)

    response = client.get("/api/auth/callback", follow_redirects=False)

    assert response.status_code == 401
    assert client.get("/api/auth/me").status_code == 401


def test_sign_in_is_refused_for_a_domain_outside_the_allow_list_even_for_an_existing_user(monkeypatch):
    import pytest
    from fastapi import HTTPException

    from app.config import get_settings

    db = SessionLocal()
    try:
        existing = {"sub": "g-dom-1", "email": "x@other.example", "email_verified": True, "name": "X"}
        user_from_google_claims(existing, db)  # signed up while the list was empty
        monkeypatch.setattr(get_settings(), "allowed_sign_in_domains", ["lab.example"])

        with pytest.raises(HTTPException) as refused:
            user_from_google_claims(existing, db)
        allowed = user_from_google_claims({"sub": "g-dom-2", "email": "x@LAB.example", "email_verified": True}, db)
    finally:
        db.close()

    assert refused.value.status_code == 403
    assert allowed.email == "x@lab.example"


def test_a_successful_callback_records_user_signed_in(monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from app.main import app
    from app.models import AuditEvent

    claims = {"sub": "g-cb-1", "email": "callback@example.com", "email_verified": True, "name": "Cb"}
    _fake_google(monkeypatch, token={"userinfo": claims})
    client = TestClient(app)

    response = client.get("/api/auth/callback", follow_redirects=False)

    assert response.status_code == 303
    assert client.get("/api/auth/me").json()["email"] == "callback@example.com"
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "callback@example.com"))
        events = db.scalars(select(AuditEvent).where(AuditEvent.action == "user.signed_in", AuditEvent.actor == str(user.id))).all()
    assert len(events) == 1 and events[0].payload_json == {"provider": "google"}


def test_the_google_client_requests_pkce(monkeypatch):
    from pydantic import SecretStr

    from app.config import get_settings
    from app.routers.auth import google_client

    monkeypatch.setattr(get_settings(), "google_client_id", "client-id")
    monkeypatch.setattr(get_settings(), "google_client_secret", SecretStr("client-secret"))

    assert google_client().client_kwargs["code_challenge_method"] == "S256"
