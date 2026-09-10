from typing import Any
import hashlib
import hmac
import secrets

from authlib.integrations.starlette_client import OAuth
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.dependencies import current_user
from app.models import ExternalIdentity, User

router = APIRouter(prefix="/auth", tags=["authentication"])


def google_client() -> Any:
    settings = get_settings()
    if not settings.google_client_id or not settings.google_client_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google login is not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.",
        )
    oauth = OAuth()
    oauth.register(
        name="google",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret.get_secret_value(),
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )
    return oauth.google


def user_from_google_claims(claims: dict[str, Any], db: Session) -> User:
    subject = claims.get("sub")
    email = claims.get("email")
    if not subject or not email or claims.get("email_verified") is not True:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Google did not provide a verified identity")

    identity = db.scalar(
        select(ExternalIdentity).where(ExternalIdentity.provider == "google", ExternalIdentity.subject == subject)
    )
    if identity:
        return identity.user

    user = db.scalar(select(User).where(User.email == email.lower()))
    if user is None:
        user = User(email=email.lower(), display_name=claims.get("name"))
        db.add(user)
        db.flush()

    db.add(ExternalIdentity(user_id=user.id, provider="google", subject=subject))
    db.commit()
    db.refresh(user)
    return user


def profile(user: User) -> dict[str, str | None]:
    return {"id": str(user.id), "email": user.email, "display_name": user.display_name}


class DevelopmentLogin(BaseModel):
    email: EmailStr
    display_name: str = Field(default="Researcher", min_length=1, max_length=200)


class PasswordAuth(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(default="Researcher", min_length=1, max_length=200)


def password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt$14$8$1${salt.hex()}${derived.hex()}"


def verify_password(password: str, encoded: str | None) -> bool:
    if not encoded or not encoded.startswith("scrypt$"):
        return False
    try:
        _, log_n, r, p, salt_hex, digest_hex = encoded.split("$")
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=2 ** int(log_n),
            r=int(r),
            p=int(p),
        )
        return hmac.compare_digest(derived.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


@router.get("/methods")
def methods() -> dict[str, bool]:
    settings = get_settings()
    return {
        "google_configured": bool(settings.google_client_id and settings.google_client_secret),
        "development_login": settings.environment == "development",
        "password_login": True,
    }


@router.get("/login")
async def login(request: Request):
    return await google_client().authorize_redirect(request, get_settings().google_redirect_uri)


@router.get("/callback")
async def callback(request: Request, db: Session = Depends(get_db)):
    client = google_client()
    token = await client.authorize_access_token(request)
    claims = token.get("userinfo")
    if claims is None:
        claims = await client.parse_id_token(request, token)
    user = user_from_google_claims(claims, db)
    request.session["user_id"] = str(user.id)
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/me")
def me(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not signed in")
    try:
        user = db.get(User, uuid.UUID(user_id))
    except (TypeError, ValueError):
        user = None
    if user is None:
        request.session.clear()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not signed in")
    return profile(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request):
    request.session.clear()


@router.post("/development/login")
def development_login(payload: DevelopmentLogin, request: Request, db: Session = Depends(get_db)):
    if get_settings().environment != "development":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    user = db.scalar(select(User).where(User.email == str(payload.email).lower()))
    if user is None:
        user = User(email=str(payload.email).lower(), display_name=payload.display_name)
        db.add(user)
        db.commit()
        db.refresh(user)
    request.session["user_id"] = str(user.id)
    return profile(user)


@router.post("/register")
def register(payload: PasswordAuth, request: Request, db: Session = Depends(get_db)):
    email = str(payload.email).lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status_code=409, detail="An account with this email already exists.")
    user = User(email=email, display_name=payload.display_name.strip(), password_hash=password_hash(payload.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    request.session["user_id"] = str(user.id)
    return profile(user)


@router.post("/login")
def password_login(payload: PasswordAuth, request: Request, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == str(payload.email).lower()))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    request.session["user_id"] = str(user.id)
    return profile(user)
