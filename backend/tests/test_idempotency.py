import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import false, func, select
from sqlalchemy.exc import IntegrityError

from app import task_queue as q
from app.agent import executor
from app.agent.arc_client import ArcRetrievedSource
from app.config import get_settings
from app.database import SessionLocal
from app.main import app
from app.models import AuditEvent, Project, ResearchRun, ResearchRunStatus, Source, SourceExcerpt, Task, User


def _project():
    with SessionLocal() as db:
        user = User(email=f"idem-{uuid.uuid4().hex}@example.test")
        db.add(user)
        db.flush()
        project = Project(owner_id=user.id, title="Idem")
        db.add(project)
        db.commit()
        return project.id


def _count(model, **where):
    with SessionLocal() as db:
        query = select(func.count()).select_from(model)
        for column, value in where.items():
            query = query.where(getattr(model, column) == value)
        return db.scalar(query)


# ---- tasks -------------------------------------------------------------------------------

def test_enqueueing_the_same_key_twice_returns_the_same_task():
    p = _project()
    with SessionLocal() as db:
        first = q.enqueue_task(db, p, "demo", {"n": 1}, idempotency_key="job-1")
        db.commit()
        second = q.enqueue_task(db, p, "demo", {"n": 2}, idempotency_key="job-1")
        db.commit()

    assert first.id == second.id
    assert _count(Task, project_id=p) == 1
    with SessionLocal() as db:
        assert db.get(Task, first.id).payload == {"n": 1}  # the original is untouched


def test_keys_are_scoped_to_the_project_and_optional():
    a, b = _project(), _project()
    with SessionLocal() as db:
        t1 = q.enqueue_task(db, a, "demo", idempotency_key="same")
        t2 = q.enqueue_task(db, b, "demo", idempotency_key="same")
        t3 = q.enqueue_task(db, a, "demo", idempotency_key="other")
        t4, t5 = q.enqueue_task(db, a, "demo"), q.enqueue_task(db, a, "demo")  # no key: always a new task
        db.commit()

    assert len({t1.id, t2.id, t3.id, t4.id, t5.id}) == 5


def test_a_duplicate_enqueue_does_not_repeat_the_audit_event():
    p = _project()
    registered = q.REQUIRED_GATES  # a gated type writes a task.blocked event when first enqueued
    from app import task_registry as registry
    from app.models import GateCode

    registry.register("idem_gated", requires_gate=GateCode.G2)(lambda task: None)
    try:
        with SessionLocal() as db:
            q.enqueue_task(db, p, "idem_gated", idempotency_key="k")
            db.commit()
            q.enqueue_task(db, p, "idem_gated", idempotency_key="k")
            db.commit()
    finally:
        registry.HANDLERS.pop("idem_gated", None)
        registered.pop("idem_gated", None)

    with SessionLocal() as db:
        actions = [e.action for e in db.scalars(select(AuditEvent).where(AuditEvent.project_id == p))]
    assert actions == ["task.blocked"]


def test_the_database_itself_rejects_a_duplicate_key():
    """Backstop for a true race between two requests that both passed the lookup."""
    p = _project()
    with SessionLocal() as db:
        db.add(Task(project_id=p, type="demo", idempotency_key="dup"))
        db.commit()
        db.add(Task(project_id=p, type="demo", idempotency_key="dup"))
        with pytest.raises(IntegrityError):
            db.commit()


def test_an_enqueue_race_returns_the_task_that_won_without_poisoning_the_transaction(monkeypatch):
    p = _project()
    with SessionLocal() as db:
        winner = Task(project_id=p, type="demo", idempotency_key="racing-key")
        db.add(winner)
        db.commit()

        scalar = db.scalar
        lookups = 0

        def hide_the_first_lookup(statement, *args, **kwargs):
            nonlocal lookups
            lookups += 1
            return None if lookups == 1 else scalar(statement, *args, **kwargs)

        monkeypatch.setattr(db, "scalar", hide_the_first_lookup)
        task = q.enqueue_task(db, p, "demo", idempotency_key="racing-key")
        db.commit()

    assert task.id == winner.id
    assert _count(Task, project_id=p) == 1


def test_a_research_run_is_enqueued_once_even_if_asked_twice():
    p = _project()
    run_id = uuid.uuid4()
    with SessionLocal() as db:
        for _ in range(2):
            q.enqueue_task(db, p, "research_run", {"run_id": str(run_id)}, idempotency_key=f"research_run:{run_id}")
        db.commit()

    assert _count(Task, project_id=p) == 1


def test_the_run_route_sets_the_idempotency_key(monkeypatch):
    monkeypatch.setattr(get_settings(), "run_research_inline", False)
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": "idem-route@example.com", "display_name": "I"})
    project_id = client.post("/api/projects", json={"title": "Keyed"}).json()["id"]

    run = client.post(f"/api/projects/{project_id}/research-runs", json={"question": "A long enough question?"}).json()

    with SessionLocal() as db:
        assert db.scalars(select(Task)).one().idempotency_key == f"research_run:{run['id']}"


# ---- ARC ingest --------------------------------------------------------------------------

def _items():
    return [
        ArcRetrievedSource("Web page", "https://a.example/1", None, None, "web_search", "page text", "Web search result"),
        ArcRetrievedSource("Paper with DOI", None, None, 2020, "scholar", "abstract", "Abstract", doi="10.1/abc"),
        ArcRetrievedSource("Title only paper", None, ["A. Author"], 2021, "scholar", "abstract", "Abstract"),
    ]


@pytest.fixture
def arc(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "arc_retrieval_enabled", True)
    monkeypatch.setattr(settings, "arc_retrieval_base_url", "http://arc.test")
    state = {"items": _items()}
    monkeypatch.setattr(executor.ArcRetrievalClient, "retrieve", lambda self, topic: list(state["items"]))
    return state


def _ingest(project_id, run):
    with SessionLocal() as db:
        project = db.get(Project, project_id)
        db_run = db.get(ResearchRun, run) if isinstance(run, uuid.UUID) else run
        result = executor._ingest_arc_sources(db, project, db_run, get_settings())
        return result


def _new_run(project_id):
    with SessionLocal() as db:
        run = ResearchRun(project_id=project_id, question="A long enough question?")
        db.add(run)
        db.commit()
        return run.id


def test_retrying_the_same_run_does_not_insert_items_twice_even_without_url_or_doi(arc):
    p = _project()
    run = _new_run(p)

    first = _ingest(p, run)
    retry = _ingest(p, run)

    assert first["added"] == 3 and retry["added"] == 0
    assert _count(Source, project_id=p) == 3
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(SourceExcerpt)) == 3
        assert all(s.ingest_key.startswith(f"arc:{run}:") for s in db.scalars(select(Source)))


def test_a_retry_that_gets_a_reordered_or_extended_result_adds_only_the_new_item(arc):
    p = _project()
    run = _new_run(p)
    _ingest(p, run)
    arc["items"] = list(reversed(_items())) + [
        ArcRetrievedSource("A brand new page", "https://b.example/2", None, None, "web_search", "new text", "Web search result")
    ]

    retry = _ingest(p, run)

    assert retry["added"] == 1 and _count(Source, project_id=p) == 4


def test_the_per_run_cap_counts_what_the_run_already_stored(arc, monkeypatch):
    monkeypatch.setattr(executor, "MAX_ARC_SOURCES_PER_RUN", 2)
    p = _project()
    run = _new_run(p)

    assert _ingest(p, run)["added"] == 2
    assert _ingest(p, run)["added"] == 0  # a retry can't push the run past its cap
    assert _count(Source, project_id=p) == 2


def test_a_different_run_may_add_a_title_only_paper_again_but_url_and_doi_items_stay_deduplicated(arc):
    p = _project()
    _ingest(p, _new_run(p))

    second = _ingest(p, _new_run(p))

    # url and DOI items are already in the project; the title-only paper is a new run's own record
    # (cross-run de-duplication of such records is M1.6).
    assert second["added"] == 1 and _count(Source, project_id=p) == 4


def test_the_database_rejects_two_sources_with_the_same_ingest_key_but_allows_many_without(arc):
    p = _project()
    with SessionLocal() as db:
        db.add_all([Source(project_id=p, title=f"Manual {i}") for i in range(3)])  # NULL keys never collide
        db.add(Source(project_id=p, title="Auto", ingest_key="arc:r:1"))
        db.commit()
        db.add(Source(project_id=p, title="Auto again", ingest_key="arc:r:1"))
        with pytest.raises(IntegrityError):
            db.commit()


def test_an_arc_ingest_race_skips_the_item_another_worker_stored(monkeypatch, arc):
    p = _project()
    run = _new_run(p)
    key = f"arc:{run}:{executor._item_identity_hash(_items()[0])}"
    with SessionLocal() as db:
        db.add(Source(project_id=p, title="Racing worker", ingest_key=key))
        db.commit()

        scalars = db.scalars
        hidden = False

        def hide_the_existing_key(statement, *args, **kwargs):
            nonlocal hidden
            if not hidden and "ingest_key" in str(statement):
                hidden = True
                return scalars(select(Source.ingest_key).where(false()))
            return scalars(statement, *args, **kwargs)

        monkeypatch.setattr(db, "scalars", hide_the_existing_key)
        result = executor._ingest_arc_sources(db, db.get(Project, p), db.get(ResearchRun, run), get_settings())

    assert result == {"status": "completed", "added": 2, "retrieved": 3}
    assert _count(Source, project_id=p) == 3


def test_a_run_resumed_after_a_crash_does_not_duplicate_retrieved_sources(arc, fake_llm):
    """The whole point: execute, 'crash' before completion, execute again, no double insert."""
    p = _project()
    run = _new_run(p)
    with SessionLocal() as db:
        db_run = db.get(ResearchRun, run)
        db_run.use_web_retrieval = True
        db.commit()

    executor.execute_research_run(run)
    sources_after_first = _count(Source, project_id=p)
    with SessionLocal() as db:  # the worker died before the task was marked done; the run is resumed
        db_run = db.get(ResearchRun, run)
        db_run.status = ResearchRunStatus.running
        db.commit()
    executor.execute_research_run(run)

    assert sources_after_first == 3 and _count(Source, project_id=p) == 3
    with SessionLocal() as db:
        actions = [e.action for e in db.scalars(select(AuditEvent).where(AuditEvent.project_id == p))]
    assert actions.count("sources.retrieved") == 1  # the retry stored nothing, so it records nothing
