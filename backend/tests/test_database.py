"""SQLite savepoints nest (plan X.37): `begin_nested()` must not commit on its own.

Uses a file database: the suite's shared in-memory connection can't show locking or durability.
"""
import sqlite3

from sqlalchemy import text

from app.config import get_settings
from app.database import database_engine


def _engine(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "database_url", f"sqlite:///{(tmp_path / 'savepoints.db').as_posix()}")
    engine = database_engine()
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE t (x INTEGER PRIMARY KEY)")
    return engine


def _rows(tmp_path):
    with sqlite3.connect(tmp_path / "savepoints.db") as raw:
        return [row[0] for row in raw.execute("SELECT x FROM t ORDER BY x")]


def test_a_savepoint_before_any_write_is_undone_by_the_outer_rollback(tmp_path, monkeypatch):
    engine = _engine(tmp_path, monkeypatch)
    with engine.connect() as conn:
        with conn.begin() as outer:
            with conn.begin_nested():
                conn.execute(text("INSERT INTO t VALUES (1)"))
            # Released but not committed: nobody else can see it yet.
            assert _rows(tmp_path) == []
            outer.rollback()
    assert _rows(tmp_path) == []
    engine.dispose()


def test_a_rolled_back_savepoint_keeps_the_rest_of_the_transaction(tmp_path, monkeypatch):
    engine = _engine(tmp_path, monkeypatch)
    with engine.connect() as conn:
        with conn.begin():
            conn.execute(text("INSERT INTO t VALUES (1)"))
            nested = conn.begin_nested()
            conn.execute(text("INSERT INTO t VALUES (2)"))
            nested.rollback()
            conn.execute(text("INSERT INTO t VALUES (3)"))
    assert _rows(tmp_path) == [1, 3]
    engine.dispose()


def test_reads_alone_still_open_no_transaction(tmp_path, monkeypatch):
    # Only savepoints open a transaction early; a plain read must not hold a lock other sessions wait on.
    engine = _engine(tmp_path, monkeypatch)
    with engine.connect() as conn:
        with conn.begin():
            conn.execute(text("SELECT count(*) FROM t"))
            assert conn.connection.driver_connection.in_transaction is False
    engine.dispose()
