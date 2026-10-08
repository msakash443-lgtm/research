from sqlalchemy import create_engine, event
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
    engine = create_engine(url, **options)
    if url.startswith("sqlite"):
        event.listen(engine, "savepoint", _begin_before_savepoint)
    return engine


def _begin_before_savepoint(conn, name) -> None:
    """Make `begin_nested()` nest on SQLite.

    The sqlite3 driver only opens a transaction before INSERT/UPDATE/DELETE, so a SAVEPOINT issued
    before any write starts a transaction of its own and its RELEASE commits it: the rows survive a
    later rollback of the surrounding work. Opening the transaction first makes the savepoint a real
    nested one. Only done here, not on every BEGIN, so plain reads still hold no lock (as before).
    """
    dbapi_connection = conn.connection.driver_connection
    if not dbapi_connection.in_transaction:
        dbapi_connection.execute("BEGIN")


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
