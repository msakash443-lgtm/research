import os

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings


def database_engine():
    test_mode = os.getenv("RESEARCH_TEST_MODE") == "true"
    options = {"pool_pre_ping": True}
    if test_mode:
        options["connect_args"] = {"check_same_thread": False}
        options["poolclass"] = StaticPool
    engine = create_engine(get_settings().database_url, **options)
    if test_mode:
        event.listen(engine, "savepoint", _begin_before_savepoint)
    return engine


def _begin_before_savepoint(conn, name) -> None:
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
