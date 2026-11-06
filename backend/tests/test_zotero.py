"""Plan M1.11.2: Zotero library two-way sync. Offline only (mock transport; no network).

The Zotero side is faked with `httpx.MockTransport` behind the real `ConnectorHttpClient`, so the
retry/rate-gate/breaker path is exercised exactly as it will be against the live API.
"""

import json
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import task_runner
from app.config import get_settings
from app.connectors.http import ConnectorHttpClient, HttpPolicy
from app.database import SessionLocal
from app.main import app
from app.models import AuditEvent, Project, Source, Task, TaskStatus, User
from app.zotero import (
    SOURCE_TYPE_TO_ZOTERO,
    ZOTERO_TO_SOURCE_TYPE,
    ZoteroClient,
    _creator_to_zotero,
    source_to_zotero_item,
    zotero_item_to_record,
)
from gate_helpers import approve_earlier_gates

USER_ID = "12345"


def _login(email):
    client = TestClient(app)
    user_id = client.post("/api/auth/development/login", json={"email": email, "display_name": "Z"}).json()["id"]
    return client, user_id


@pytest.fixture
def zotero(monkeypatch):
    """Zotero sync switched on and configured (the default is off; see test_zotero_not_configured_*)."""
    monkeypatch.setenv("ZOTERO_SYNC_ENABLED", "true")
    monkeypatch.setenv("ZOTERO_API_KEY", "test-key")
    monkeypatch.setenv("ZOTERO_USER_ID", USER_ID)
    monkeypatch.setenv("ZOTERO_COLLECTION_KEY", "COLL")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def make_library(initial_items=(), get_failures=(), post_failures=()):
    """A one-user Zotero library on a mock transport. Returns (client, state, seen_requests)."""
    state = {
        "items": [dict(item) for item in initial_items],
        "next_key": 1000,
        "get_failures": list(get_failures),
        "post_failures": list(post_failures),
    }
    seen = []

    def handler(request):
        seen.append(request)
        if request.method == "GET" and request.url.path == f"/users/{USER_ID}/items":
            if state["get_failures"]:
                return state["get_failures"].pop(0)
            limit = int(request.url.params.get("limit", "100"))
            start = int(request.url.params.get("start", "0"))
            return httpx.Response(200, json=state["items"][start:start + limit])
        if request.method == "POST" and request.url.path == f"/users/{USER_ID}/items":
            if state["post_failures"]:
                return state["post_failures"].pop(0)
            body = json.loads(request.content)
            key = f"K{state['next_key']:04d}"
            state["next_key"] += 1
            state["items"].append({"key": key, **body})
            return httpx.Response(201, headers={"Location": f"https://api.zotero.org/users/{USER_ID}/items/{key}"})
        return httpx.Response(404)

    http = ConnectorHttpClient(
        "zotero", "https://api.zotero.org", HttpPolicy(max_retries=0, min_interval_seconds=0),
        transport=httpx.MockTransport(handler),
    )
    return ZoteroClient("test-key", USER_ID, http=http), state, seen


@pytest.fixture
def world(zotero, monkeypatch):
    """A project with its owner, a fake library wired into the task handlers, and a gate-approved pull."""
    client, owner_id = _login(f"zot-{uuid.uuid4().hex[:8]}@example.com")
    project_id = client.post("/api/projects", json={"title": "Zotero sync"}).json()["id"]
    lib_client, state, seen = make_library()
    monkeypatch.setattr("app.task_handlers.build_zotero_client", lambda: lib_client)
    approve_earlier_gates(project_id, "G2")
    assert client.post(f"/api/projects/{project_id}/gates/G2/approve", json={"note": "ok"}).status_code == 200
    return {"client": client, "owner_id": owner_id, "project": project_id, "library": state, "seen": seen}


def zotero_item(key, item_type="journalArticle", title="A study", **extra):
    item = {"key": key, "itemType": item_type, "title": title}
    item.update(extra)
    return item


def add_source(project_id, title, verified=False, **kw):
    with SessionLocal() as db:
        source = Source(project_id=uuid.UUID(project_id), title=title, metadata_verified=verified, **kw)
        db.add(source)
        db.commit()
        return source.id


def sources_of(project_id):
    with SessionLocal() as db:
        rows = db.scalars(select(Source).where(Source.project_id == uuid.UUID(project_id))).all()
        db.expunge_all()
        return rows


def audits_of(project_id):
    with SessionLocal() as db:
        rows = db.scalars(select(AuditEvent).where(AuditEvent.project_id == uuid.UUID(project_id))).all()
        db.expunge_all()
        return {event.action: event for event in rows}


def task_after_run(project_id, task_type):
    with SessionLocal() as db:
        task = db.scalar(select(Task).where(Task.project_id == uuid.UUID(project_id), Task.type == task_type))
        db.expunge(task)
        return task


def queue_pull(world):
    return world["client"].post(f"/api/projects/{world['project']}/zotero/pull")


def queue_push(world):
    return world["client"].post(f"/api/projects/{world['project']}/zotero/push")


# --- configuration and status ------------------------------------------------


def test_status_reports_configuration_and_counts(world):
    add_source(world["project"], "Verified, synced", verified=True, source_ids={"zotero": "K7"})
    add_source(world["project"], "Verified, not synced", verified=True)
    add_source(world["project"], "Unverified")

    status = world["client"].get(f"/api/projects/{world['project']}/zotero").json()

    assert status == {
        "enabled": True, "configured": True, "library": f"user:{USER_ID}", "collection_key": "COLL",
        "sources_total": 3, "sources_verified": 2, "sources_synced": 1,
    }


def test_endpoints_refuse_with_409_while_not_configured(monkeypatch):
    get_settings.cache_clear()  # default settings: zotero off
    client, _ = _login(f"zot-off-{uuid.uuid4().hex[:8]}@example.com")
    project_id = client.post("/api/projects", json={"title": "Off"}).json()["id"]

    pulled = client.post(f"/api/projects/{project_id}/zotero/pull")
    pushed = client.post(f"/api/projects/{project_id}/zotero/push")

    assert pulled.status_code == 409 and pushed.status_code == 409
    assert "ZOTERO_SYNC_ENABLED" in pulled.json()["detail"]
    assert not sources_of(project_id)
    get_settings.cache_clear()


def test_enabled_without_credentials_is_a_startup_error(monkeypatch):
    monkeypatch.delenv("ZOTERO_API_KEY", raising=False)
    monkeypatch.delenv("ZOTERO_USER_ID", raising=False)
    get_settings.cache_clear()
    from app.config import Settings

    with pytest.raises(ValueError, match="ZOTERO_SYNC_ENABLED=true requires"):
        Settings(zotero_sync_enabled=True, environment="development")
    get_settings.cache_clear()


# --- pull --------------------------------------------------------------------


def test_pull_imports_items_unverified_with_provenance(world):
    world["library"]["items"] = [
        zotero_item(
            "K1", "journalArticle", title="The \n  long\ttitle",
            creators=[{"creatorType": "author", "lastName": "Smith", "firstName": "Jane"},
                      {"creatorType": "author", "name": "Max Planck Society"}],
            date="2020-05-01", DOI="https://doi.org/10.1000/TEST", URL="https://x.example/paper",
            publicationTitle="Journal of Tests", abstractNote="An abstract.",
        ),
        zotero_item("K2", "bookSection", title="A chapter", creators=[], date="2019", bookTitle="The Book"),
    ]

    queued = queue_pull(world)
    assert queued.status_code == 202 and queued.json()["status"] == "queued"
    assert task_runner.run_one_task() is True

    rows = {s.title for s in sources_of(world["project"])}
    assert rows == {"The long title", "A chapter"}
    first = next(s for s in sources_of(world["project"]) if s.title == "The long title")
    assert first.metadata_verified is False and first.origin == "retrieved"
    assert first.created_by == "agent:zotero-sync"
    assert first.ingest_key == "zotero:K1" and first.source_ids == {"zotero": "K1"}
    assert first.doi == "10.1000/test" and first.year == 2020 and first.url == "https://x.example/paper"
    assert first.authors == ["Smith, Jane", "Max Planck Society"]
    assert first.venue == "Journal of Tests" and first.abstract == "An abstract." and first.source_type == "article"
    chapter = next(s for s in sources_of(world["project"]) if s.title == "A chapter")
    assert chapter.source_type == "chapter" and chapter.venue == "The Book"

    events = audits_of(world["project"])
    assert "zotero.pull_requested" in events and "zotero.pulled" in events
    pulled = events["zotero.pulled"]
    assert pulled.actor == "agent:zotero-sync"
    assert pulled.payload_json == {
        "library": f"user:{USER_ID}", "collection": "COLL", "total": 2, "added": 2,
        "skipped_existing": 0, "skipped_duplicate_doi": 0, "skipped_no_title": 0,
    }


def test_pull_starts_blocked_until_a_person_approves_g2(world):
    world["library"]["items"] = [zotero_item("K1")]
    # Undo the fixture's G2 approval so the pull is genuinely waiting.
    with SessionLocal() as db:
        from app.models import Gate, GateStatus

        db.query(Gate).filter(Gate.project_id == uuid.UUID(world["project"])).update({"status": GateStatus.pending})
        db.commit()

    queued = queue_pull(world)
    body = queued.json()

    assert queued.status_code == 202 and body["status"] == "blocked" and body["blocked_by_gate"] == "G2"
    assert task_runner.run_one_task() is False  # nothing may run while G2 is pending
    assert task_after_run(world["project"], "zotero_pull").status == TaskStatus.blocked

    approve_earlier_gates(world["project"], "G2")
    assert world["client"].post(f"/api/projects/{world['project']}/gates/G2/approve", json={"note": "ok"}).status_code == 200
    assert task_after_run(world["project"], "zotero_pull").status == TaskStatus.queued  # the approval releases it
    assert task_runner.run_one_task() is True
    assert task_after_run(world["project"], "zotero_pull").status == TaskStatus.completed
    assert len(sources_of(world["project"])) == 1


def test_pull_skips_duplicate_dois_and_items_without_titles(world):
    add_source(world["project"], "Already here", doi="10.1000/test", source_type="article")
    world["library"]["items"] = [
        zotero_item("K1", DOI="10.1000/Test", title="Same paper, from Zotero"),
        zotero_item("K2", title=""),  # no title -> reported, never invented
        zotero_item("K3", title="New paper", DOI="10.1111/NEW"),
    ]

    queue_pull(world)
    assert task_runner.run_one_task() is True

    rows = sources_of(world["project"])
    assert {s.title for s in rows} == {"Already here", "New paper"}
    pulled = audits_of(world["project"])["zotero.pulled"].payload_json
    assert pulled["added"] == 1 and pulled["skipped_duplicate_doi"] == 1 and pulled["skipped_no_title"] == 1


def test_retried_pull_adds_nothing(world):
    world["library"]["items"] = [zotero_item("K1", title="One"), zotero_item("K2", title="Two")]

    queue_pull(world)
    assert task_runner.run_one_task() is True
    first = audits_of(world["project"])["zotero.pulled"].payload_json

    queue_pull(world)
    assert task_runner.run_one_task() is True
    second = audits_of(world["project"])["zotero.pulled"]  # the later event now
    # two events: audit map keeps the last one
    assert second.payload_json == {
        "library": f"user:{USER_ID}", "collection": "COLL", "total": 2, "added": 0,
        "skipped_existing": 2, "skipped_duplicate_doi": 0, "skipped_no_title": 0,
    }
    assert first["added"] == 2
    assert len(sources_of(world["project"])) == 2


def test_pull_paginates_the_library(world, monkeypatch):
    monkeypatch.setattr("app.zotero.PAGE_SIZE", 2)
    world["library"]["items"] = [zotero_item(f"K{i}", title=f"Paper {i}") for i in range(5)]

    queue_pull(world)
    assert task_runner.run_one_task() is True

    gets = [r for r in world["seen"] if r.method == "GET"]
    assert [(r.url.params.get("start"), r.url.params.get("limit")) for r in gets] == [("0", "2"), ("2", "2"), ("4", "2")]
    assert len(sources_of(world["project"])) == 5


def test_pull_caps_at_max_items(world, monkeypatch):
    monkeypatch.setattr("app.zotero.MAX_ITEMS", 3)
    world["library"]["items"] = [zotero_item(f"K{i}", title=f"Paper {i}") for i in range(5)]

    queue_pull(world)
    assert task_runner.run_one_task() is True

    pulled = audits_of(world["project"])["zotero.pulled"].payload_json
    assert pulled["total"] == 3 and pulled["added"] == 3
    assert {s.title for s in sources_of(world["project"])} == {"Paper 0", "Paper 1", "Paper 2"}


def test_pull_failure_fails_loudly_and_writes_nothing(world):
    world["library"]["get_failures"] = [httpx.Response(401)]
    world["library"]["items"] = [zotero_item("K1")]

    queue_pull(world)
    assert task_runner.run_one_task() is True  # the attempt ran; it failed loudly

    task = task_after_run(world["project"], "zotero_pull")
    assert task.status == TaskStatus.failed and "401" in (task.error or "")
    assert not sources_of(world["project"])
    assert "zotero.pulled" not in audits_of(world["project"])


# --- push --------------------------------------------------------------------


def test_push_sends_only_verified_sources(world):
    verified_id = add_source(
        world["project"], "A verified paper", verified=True, source_type="article",
        authors=["Smith, Jane"], year=2020, doi="10.1000/test", url="https://x.example/paper",
        venue="Journal of Tests", abstract="An abstract.",
    )
    add_source(world["project"], "Not verified yet", verified=False, doi="10.2000/other")

    queued = queue_push(world)
    assert queued.status_code == 202 and queued.json() == {"task_id": queued.json()["task_id"], "status": "queued", "blocked_by_gate": None}
    assert task_runner.run_one_task() is True

    posts = [json.loads(r.content) for r in world["seen"] if r.method == "POST"]
    assert len(posts) == 1
    assert posts[0] == {
        "itemType": "journalArticle", "title": "A verified paper",
        "creators": [{"creatorType": "author", "lastName": "Smith", "firstName": "Jane"}],
        "date": "2020", "DOI": "10.1000/test", "URL": "https://x.example/paper",
        "publicationTitle": "Journal of Tests", "abstractNote": "An abstract.",
    }
    rows = {s.id: s for s in sources_of(world["project"])}
    assert rows[verified_id].source_ids == {"zotero": "K1000"}
    pushed = audits_of(world["project"])["zotero.pushed"].payload_json
    assert pushed == {
        "library": f"user:{USER_ID}", "collection": "COLL", "total_verified": 1, "created": 1,
        "adopted": 0, "skipped_existing": 0, "unverified_skipped": 1,
    }


def test_push_adopts_an_item_an_earlier_crashed_attempt_already_created(world):
    world["library"]["items"] = [zotero_item("Z9", DOI="10.1000/test", title="A verified paper")]
    add_source(world["project"], "A verified paper", verified=True, doi="10.1000/test")

    queue_push(world)
    assert task_runner.run_one_task() is True

    assert [r for r in world["seen"] if r.method == "POST"] == []  # adopted, not duplicated
    (row,) = sources_of(world["project"])
    assert row.source_ids == {"zotero": "Z9"}
    pushed = audits_of(world["project"])["zotero.pushed"].payload_json
    assert pushed["created"] == 0 and pushed["adopted"] == 1 and pushed["skipped_existing"] == 0


def test_push_skips_sources_that_already_have_a_zotero_key(world):
    add_source(world["project"], "Already pushed", verified=True, doi="10.3000/one", source_ids={"zotero": "K9"})
    add_source(world["project"], "Not pushed yet", verified=True, doi="10.3000/two")

    queue_push(world)
    assert task_runner.run_one_task() is True

    posts = [json.loads(r.content) for r in world["seen"] if r.method == "POST"]
    assert [p["title"] for p in posts] == ["Not pushed yet"]
    rows = {s.title: s for s in sources_of(world["project"])}
    assert rows["Already pushed"].source_ids == {"zotero": "K9"}
    assert rows["Not pushed yet"].source_ids == {"zotero": "K1000"}
    pushed = audits_of(world["project"])["zotero.pushed"].payload_json
    assert pushed["created"] == 1 and pushed["skipped_existing"] == 1


def test_push_creates_nothing_when_nothing_is_verified(world):
    add_source(world["project"], "Still unverified", verified=False)

    queue_push(world)
    assert task_runner.run_one_task() is True

    assert [r for r in world["seen"] if r.method == "POST"] == []
    pushed = audits_of(world["project"])["zotero.pushed"].payload_json
    assert pushed["total_verified"] == 0 and pushed["created"] == 0 and pushed["unverified_skipped"] == 1


def test_push_failure_writes_nothing(world):
    add_source(world["project"], "A verified paper", verified=True, doi="10.1000/test")
    world["library"]["post_failures"] = [httpx.Response(400)]

    queue_push(world)
    assert task_runner.run_one_task() is True

    task = task_after_run(world["project"], "zotero_push")
    assert task.status == TaskStatus.failed and "400" in (task.error or "")
    (row,) = sources_of(world["project"])
    assert row.source_ids is None
    assert "zotero.pushed" not in audits_of(world["project"])


# --- mapping (pure) ------------------------------------------------------------


def test_item_type_maps_are_complete_and_total():
    assert ZOTERO_TO_SOURCE_TYPE["journalArticle"] == "article"
    assert ZOTERO_TO_SOURCE_TYPE["bookSection"] == "chapter"
    assert SOURCE_TYPE_TO_ZOTERO["chapter"] == "bookSection"
    assert zotero_item_to_record(zotero_item("K1", "tvShow", title="Unknown type"))["source_type"] == "other"
    assert zotero_item_to_record({"key": "K1", "itemType": "journalArticle", "title": "  "}) is None


def test_creator_name_round_trip():
    assert _creator_to_zotero("Smith, Jane") == {"creatorType": "author", "lastName": "Smith", "firstName": "Jane"}
    assert _creator_to_zotero("Max Planck Society") == {"creatorType": "author", "name": "Max Planck Society"}
    assert _creator_to_zotero("  ") == {"creatorType": "author", "name": ""}


def test_source_to_zotero_item_places_venue_by_type():
    with SessionLocal() as db:
        owner = User(email=f"zot-map-{uuid.uuid4().hex[:8]}@example.com", display_name="M")
        db.add(owner)
        db.flush()
        project = Project(title="T", owner_id=owner.id)
        db.add(project)
        db.commit()
        for source_type, field in (
            ("article", "publicationTitle"), ("chapter", "bookTitle"),
            ("conference", "proceedingsTitle"), ("book", "publisher"),
        ):
            source = Source(project_id=project.id, title="T", source_type=source_type, venue="V")
            db.add(source)
        db.commit()
        rows = {s.source_type: source_to_zotero_item(s) for s in db.scalars(select(Source)).all()}
    for source_type, field in (
        ("article", "publicationTitle"), ("chapter", "bookTitle"),
        ("conference", "proceedingsTitle"), ("book", "publisher"),
    ):
        assert rows[source_type].get(field) == "V"
    assert all("date" not in item for item in rows.values())  # no year -> no date field
