"""Append-only triggers and the gate row lock on a real PostgreSQL database (plan X.44).

On SQLite the triggers are SQLite syntax and every session shares one connection, so the suite can't show
that the PostgreSQL trigger functions refuse a write or that `FOR UPDATE` serialises two requests. These
tests need `TEST_POSTGRES_URL` (CI sets it; see ci.yml) and are skipped without it, e.g.

    TEST_POSTGRES_URL=postgresql+psycopg://user:pass@localhost:5432/dbname pytest -q -n 0 backend/tests/test_guards_postgres.py

The database named there is wiped: use a throwaway one. Run with `-n 0` (see test_task_queue_postgres.py).
Writes here go through raw SQL, which skips the ORM guards, so only the database triggers can refuse them.
"""

import os
import threading
import time
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from app import audit_guard, excerpt_guard, note_guard  # noqa: F401  (importing registers the trigger DDL)
from app.database import Base
from app.models import (
    AuditEvent, Gate, GateCode, GateStatus, Note, NoteKind, NoteRevision, Project, Source, SourceExcerpt, User,
    excerpt_hash,
)
from app.routers.gates import decide_gate, reopen_gate
from app.schemas import GateReopen

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")

pytestmark = pytest.mark.skipif(
    not POSTGRES_URL, reason="TEST_POSTGRES_URL not set — these tests need a real Postgres instance (see ci.yml)"
)


@pytest.fixture
def pg():
    """An engine and sessionmaker on a fresh schema in the Postgres database."""
    engine = create_engine(POSTGRES_URL, pool_pre_ping=True, pool_size=10)
    assert engine.dialect.name == "postgresql", "TEST_POSTGRES_URL must point at PostgreSQL"
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
    yield engine, Session
    Base.metadata.drop_all(engine)
    engine.dispose()


def _user_and_project(Session):
    with Session() as db:
        user = User(email=f"pg-{uuid.uuid4().hex}@example.test")
        db.add(user)
        db.flush()
        project = Project(owner_id=user.id, title="Postgres guards")
        db.add(project)
        db.commit()
        return user.id, project.id


def _scalar(engine, sql, **params):
    with engine.connect() as conn:
        return conn.execute(text(sql), params).scalar_one()


def _refused(engine, sql, message, **params):
    """Run `sql` in its own transaction and expect the database to refuse it with `message`."""
    with pytest.raises(DBAPIError, match=message):
        with engine.begin() as conn:
            conn.execute(text(sql), params)


# --- append-only / immutable triggers ---------------------------------------------------------------


def test_audit_events_refuse_update_delete_and_truncate(pg):
    engine, Session = pg
    with Session() as db:
        event = AuditEvent(actor="agent:test", action="demo.created")
        db.add(event)
        db.commit()
        event_id = event.id

    for sql in (
        "UPDATE audit_events SET action = 'tampered' WHERE id = :id",
        "DELETE FROM audit_events WHERE id = :id",
        "TRUNCATE audit_events",
    ):
        params = {} if sql.startswith("TRUNCATE") else {"id": event_id}
        _refused(engine, sql, audit_guard.APPEND_ONLY_MESSAGE, **params)

    assert _scalar(engine, "SELECT action FROM audit_events WHERE id = :id", id=event_id) == "demo.created"


def test_note_revisions_refuse_update_delete_and_truncate(pg):
    engine, Session = pg
    owner_id, _ = _user_and_project(Session)
    with Session() as db:
        note = Note(
            owner_id=owner_id, client_id="c1", kind=next(iter(NoteKind)), body="first",
            captured_at=datetime.now(timezone.utc),
        )
        db.add(note)
        db.flush()
        revision = NoteRevision(note_id=note.id, revision=1, body="first")
        db.add(revision)
        db.commit()
        revision_id = revision.id

    for sql in (
        "UPDATE note_revisions SET body = 'tampered' WHERE id = :id",
        "DELETE FROM note_revisions WHERE id = :id",
        "TRUNCATE note_revisions",
    ):
        params = {} if sql.startswith("TRUNCATE") else {"id": revision_id}
        _refused(engine, sql, note_guard.APPEND_ONLY_MESSAGE, **params)

    assert _scalar(engine, "SELECT body FROM note_revisions WHERE id = :id", id=revision_id) == "first"


def test_excerpt_content_and_hash_are_immutable_but_locator_is_not(pg):
    engine, Session = pg
    _, project_id = _user_and_project(Session)
    with Session() as db:
        source = Source(project_id=project_id, title="A paper")
        db.add(source)
        db.flush()
        excerpt = SourceExcerpt(source_id=source.id, content="quoted", content_hash=excerpt_hash("quoted"), locator="p. 1")
        db.add(excerpt)
        db.commit()
        excerpt_id = excerpt.id

    _refused(engine, "UPDATE source_excerpts SET content = 'edited' WHERE id = :id", excerpt_guard.IMMUTABLE_MESSAGE, id=excerpt_id)
    _refused(engine, "UPDATE source_excerpts SET content_hash = 'x' WHERE id = :id", excerpt_guard.IMMUTABLE_MESSAGE, id=excerpt_id)
    assert _scalar(engine, "SELECT content FROM source_excerpts WHERE id = :id", id=excerpt_id) == "quoted"

    # Only content and its hash are protected; correcting a locator is allowed, as is a no-op write of the same content.
    with engine.begin() as conn:
        conn.execute(text("UPDATE source_excerpts SET locator = 'p. 2', content = 'quoted' WHERE id = :id"), {"id": excerpt_id})
    assert _scalar(engine, "SELECT locator FROM source_excerpts WHERE id = :id", id=excerpt_id) == "p. 2"


# --- gate decide / reopen row lock (M0.5.13) --------------------------------------------------------


@pytest.fixture
def gated(pg):
    """A project whose G1 is approved and G2 is pending; yields (Session, user_id, project_id)."""
    _, Session = pg
    user_id, project_id = _user_and_project(Session)
    with Session() as db:
        db.add_all(Gate(project_id=project_id, code=code) for code in GateCode)
        db.flush()
        g1 = db.scalar(select(Gate).where(Gate.project_id == project_id, Gate.code == GateCode.G1))
        g1.status = GateStatus.approved
        db.commit()
    return Session, user_id, project_id


def _approve_g2(Session, user_id, project_id):
    with Session() as db:
        return decide_gate(db, db.get(Project, project_id), db.get(User, user_id), GateCode.G2, GateStatus.approved, None)


def _reopen_g1(Session, user_id, project_id):
    with Session() as db:
        return reopen_gate(GateCode.G1, GateReopen(reason="take it back"), db.get(Project, project_id), db.get(User, user_id), db)


def _statuses(Session, project_id):
    with Session() as db:
        return {g.code: g.status for g in db.scalars(select(Gate).where(Gate.project_id == project_id))}


def _run(fn, Session, user_id, project_id):
    """Run `fn` in a thread; the result is ('ok', value) or ('http', status_code), or ('error', exc)."""
    outcome = []

    def target():
        try:
            outcome.append(("ok", fn(Session, user_id, project_id)))
        except HTTPException as exc:
            outcome.append(("http", exc.status_code))
        except Exception as exc:  # surfaced by the caller; a thread can't fail the test itself
            outcome.append(("error", exc))

    thread = threading.Thread(target=target)
    thread.start()
    return thread, outcome


@pytest.mark.parametrize("action", [_approve_g2, _reopen_g1], ids=["decide", "reopen"])
def test_decide_and_reopen_wait_for_the_project_row_lock(gated, action):
    Session, user_id, project_id = gated
    with Session() as holder:
        holder.refresh(holder.get(Project, project_id), with_for_update=True)  # another request holds the lock

        thread, outcome = _run(action, Session, user_id, project_id)
        time.sleep(1.5)
        assert thread.is_alive() and not outcome  # blocked on the lock, not racing past it

        holder.commit()  # release
    thread.join(timeout=30)
    assert not thread.is_alive() and [kind for kind, _ in outcome] == ["ok"]


def test_a_concurrent_decide_and_reopen_never_leave_a_later_gate_approved_over_a_pending_one(gated, pg):
    Session, user_id, project_id = gated
    engine, _ = pg
    start = threading.Barrier(2, timeout=30)

    def gated_call(fn):
        def run(*args):
            start.wait()  # both requests begin together
            return fn(*args)
        return run

    for _ in range(5):
        start.reset()
        a, a_out = _run(gated_call(_approve_g2), Session, user_id, project_id)
        b, b_out = _run(gated_call(_reopen_g1), Session, user_id, project_id)
        a.join(timeout=30)
        b.join(timeout=30)
        assert not a.is_alive() and not b.is_alive()

        results = [a_out[0], b_out[0]]
        assert all(kind in ("ok", "http") for kind, _ in results), results
        assert sorted(kind for kind, _ in results) == ["http", "ok"]  # exactly one wins ...
        assert [value for kind, value in results if kind == "http"] == [409]  # ... the other gets the ordinary conflict
        status = _statuses(Session, project_id)
        assert not (status[GateCode.G1] != GateStatus.approved and status[GateCode.G2] == GateStatus.approved)

        # Put the fixture state back for the next round.
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE gates SET status = 'pending', decided_by = NULL, decided_at = NULL WHERE project_id = :p AND code = 'G2'"),
                {"p": project_id},
            )
            conn.execute(text("UPDATE gates SET status = 'approved' WHERE project_id = :p AND code = 'G1'"), {"p": project_id})


def test_two_concurrent_approvals_of_one_gate_give_one_winner_and_one_409(gated):
    Session, user_id, project_id = gated
    start = threading.Barrier(2, timeout=30)

    def approve(*args):
        start.wait()
        return _approve_g2(*args)

    a, a_out = _run(approve, Session, user_id, project_id)
    b, b_out = _run(approve, Session, user_id, project_id)
    a.join(timeout=30)
    b.join(timeout=30)

    results = [a_out[0], b_out[0]]
    assert sorted(kind for kind, _ in results) == ["http", "ok"], results
    assert [value for kind, value in results if kind == "http"] == [409]
    with Session() as db:
        events = [e.action for e in db.scalars(select(AuditEvent).where(AuditEvent.project_id == project_id))]
    assert events.count("gate.approved") == 1  # the loser wrote no audit row
