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
