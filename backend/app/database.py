from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings


def database_engine():
    url = get_settings().database_url
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    options = {"connect_args": connect_args, "pool_pre_ping": not url.startswith("sqlite")}
    # A single in-memory connection keeps FastAPI TestClient and its worker thread on the same test database.
    if url in {"sqlite://", "sqlite+pysqlite://", "sqlite:///:memory:"}:
        options["poolclass"] = StaticPool
    return create_engine(url, **options)


engine = database_engine()
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
