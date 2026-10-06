import hashlib
import uuid

import pytest
from fastapi.testclient import TestClient

from app.agent import executor
from app.agent.arc_client import ArcRetrievalError, ArcRetrievedSource, _normalize
from app.config import Settings, get_settings
from app.database import SessionLocal
from app.main import app
from app.prompt_registry import load_prompt
from app.models import ARC_SOURCE_TYPES, ResearchRun, Source, SourceExcerpt

QUESTION = "What does the evidence say about female labour participation?"


def development_client() -> tuple[TestClient, str]:
    client = TestClient(app)
    login = client.post("/api/auth/development/login", json={"email": "arc@example.com", "display_name": "ARC Tester"})
    assert login.status_code == 200
    project = client.post("/api/projects", json={"title": "Retrieval research"})
    assert project.status_code == 201
    return client, project.json()["id"]


@pytest.fixture
def arc_enabled(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "arc_retrieval_enabled", True)
    monkeypatch.setattr(settings, "arc_retrieval_base_url", "http://arc.test")
    return settings


def test_web_retrieval_is_rejected_when_not_enabled():
    client, project_id = development_client()

    response = client.post(
        f"/api/projects/{project_id}/research-runs", json={"question": QUESTION, "use_web_retrieval": True}
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Web retrieval is not enabled on this deployment."


def test_default_run_does_not_call_retrieval(arc_enabled, monkeypatch):
    def fail(*_args, **_kwargs):
        raise AssertionError("retrieval must not run unless the run opts in")

    monkeypatch.setattr(executor.ArcRetrievalClient, "retrieve", fail)
    client, project_id = development_client()

    response = client.post(f"/api/projects/{project_id}/research-runs", json={"question": QUESTION})

    assert response.status_code == 202
    assert response.json()["use_web_retrieval"] is False
    assert response.json()["status"] == "needs_sources"


def test_retrieved_sources_are_saved_deduplicated_and_unverified(arc_enabled, monkeypatch):
    client, project_id = development_client()
    existing = client.post(
        f"/api/projects/{project_id}/sources",
        json={"title": "Existing report", "url": "https://example.gov/report", "evidence_excerpt": "Manual note."},
    )
    assert existing.status_code == 201

    retrieved = [
        ArcRetrievedSource(
            title="Duplicate of existing", url="https://example.gov/report", authors=None, year=None,
            source_type="web_search", excerpt="Should be skipped.", locator="Web search result",
        ),
        ArcRetrievedSource(
            title="Labour survey paper", url="https://example.org/paper", authors=["A. Author"], year=2023,
            source_type="scholar", excerpt="Participation rose   between surveys.", locator="Google Scholar abstract",
        ),
    ]
    monkeypatch.setattr(executor.ArcRetrievalClient, "retrieve", lambda self, topic: retrieved)

    response = client.post(
        f"/api/projects/{project_id}/research-runs", json={"question": QUESTION, "use_web_retrieval": True}
    )

    assert response.status_code == 202
    body = response.json()
    assert body["use_web_retrieval"] is True
    # No LLM is configured in tests, so a run with evidence stops here.
    assert body["status"] == "needs_configuration"

    with SessionLocal() as db:
        project_key = uuid.UUID(project_id)
        sources = db.query(Source).filter(Source.project_id == project_key).all()
        assert len(sources) == 2
        new = next(source for source in sources if source.url == "https://example.org/paper")
        assert new.metadata_verified is False
        assert new.source_type == "scholar"
        assert new.authors == ["A. Author"]
        excerpt = db.query(SourceExcerpt).filter(SourceExcerpt.source_id == new.id).one()
        assert excerpt.content == "Participation rose between surveys."
        assert excerpt.content_hash == hashlib.sha256(excerpt.content.encode("utf-8")).hexdigest()

        run = db.get(ResearchRun, uuid.UUID(body["id"]))
        assert run.input_snapshot["web_retrieval"] == {"status": "completed", "added": 1, "retrieved": 2}
        automated = {source["title"]: source["automated"] for source in run.input_snapshot["sources"]}
        assert automated == {"Existing report": False, "Labour survey paper": True}

    listed = client.get(f"/api/projects/{project_id}/sources").json()
    assert {source["source_type"] for source in listed} == {"article", "scholar"}


def test_retrieved_excerpt_reaches_the_runs_evidence(arc_enabled, monkeypatch):
    # The executor ingests and then reads sources in one session; the excerpt written
    # during ingest must still be eager-loaded into the snapshot (plan X.21.1).
    retrieved = [
        ArcRetrievedSource(
            title="Only retrieved paper", url="https://example.org/only", authors=None, year=2024,
            source_type="scholar", excerpt="Retrieved   finding text.", locator="Google Scholar abstract",
        ),
    ]
    monkeypatch.setattr(executor.ArcRetrievalClient, "retrieve", lambda self, topic: retrieved)
    client, project_id = development_client()

    response = client.post(
        f"/api/projects/{project_id}/research-runs", json={"question": QUESTION, "use_web_retrieval": True}
    )

    assert response.status_code == 202
    # With the excerpt lost, the run would stop at needs_sources instead.
    assert response.json()["status"] == "needs_configuration"
    with SessionLocal() as db:
        run = db.get(ResearchRun, uuid.UUID(response.json()["id"]))
        [source] = run.input_snapshot["sources"]
        assert source["excerpt"] == "Retrieved finding text."
        assert source["locator"] == "Google Scholar abstract"
        assert source["automated"] is True


def test_manual_sources_come_first_and_survive_the_source_cap(arc_enabled, monkeypatch):
    monkeypatch.setattr(executor, "MAX_SOURCES", 3)
    monkeypatch.setattr(executor, "MAX_TOTAL_EVIDENCE_CHARS", 30)
    client, project_id = development_client()
    for title in ("Manual one", "Manual two"):
        created = client.post(
            f"/api/projects/{project_id}/sources",
            json={"title": title, "evidence_excerpt": "Manual evidence."},
        )
        assert created.status_code == 201

    retrieved = [
        ArcRetrievedSource(
            title=f"Found {index}", url=f"https://example.org/{index}", authors=None, year=None,
            source_type="web_search", excerpt="Retrieved evidence.", locator="Web search result",
        )
        for index in range(5)
    ]
    monkeypatch.setattr(executor.ArcRetrievalClient, "retrieve", lambda self, topic: retrieved)

    response = client.post(
        f"/api/projects/{project_id}/research-runs", json={"question": QUESTION, "use_web_retrieval": True}
    )

    assert response.status_code == 202
    with SessionLocal() as db:
        run = db.get(ResearchRun, uuid.UUID(response.json()["id"]))
        snapshot = run.input_snapshot["sources"]
    # The newer retrieved sources must not displace manual evidence from the 3-source cap.
    # (The two manual rows can share a second-resolution timestamp, so compare them as a set.)
    assert {source["title"] for source in snapshot[:2]} == {"Manual one", "Manual two"}
    assert [source["automated"] for source in snapshot] == [False, False, True]
    # Manual excerpts get first claim on the evidence budget (16 + 14 chars of 30).
    assert sorted(source["excerpt"] for source in snapshot[:2]) == ["Manual evidenc", "Manual evidence."]
    assert snapshot[2]["excerpt"] == ""


def test_retrieval_failure_does_not_fail_the_run(arc_enabled, monkeypatch):
    def unreachable(self, topic):
        raise ArcRetrievalError("The ARC retrieval service could not be reached.")

    monkeypatch.setattr(executor.ArcRetrievalClient, "retrieve", unreachable)
    client, project_id = development_client()

    response = client.post(
        f"/api/projects/{project_id}/research-runs", json={"question": QUESTION, "use_web_retrieval": True}
    )

    assert response.status_code == 202
    assert response.json()["status"] == "needs_sources"
    with SessionLocal() as db:
        run = db.get(ResearchRun, uuid.UUID(response.json()["id"]))
        assert run.input_snapshot["web_retrieval"]["status"] == "failed"


def test_prompt_labels_automated_sources():
    run = ResearchRun(question=QUESTION)
    sources = [
        {"title": "Manual", "excerpt": "A.", "automated": False},
        {"title": "Found online", "excerpt": "B.", "automated": True},
    ]

    prompt = load_prompt(*executor.EVIDENCE_SYNTHESIS_PROMPT)
    _system, user = executor._agent_prompts(prompt, run, [], sources)

    assert "<<<SOURCE [S1] id=" in user and "Title: Manual\nStatus: added by a researcher | NOT VERIFIED — do not cite this source." in user
    assert "<<<SOURCE [S2] id=" in user and "Title: Found online\nStatus: automatically retrieved | NOT VERIFIED — do not cite this source." in user


def test_normalize_drops_malformed_and_non_http_items():
    items = _normalize(
        {
            "web_results": [
                {"title": "Good", "url": "https://example.org/a", "snippet": "snippet", "content": None},
                {"title": "Local file", "url": "file:///etc/passwd", "snippet": "x"},
                {"title": "", "url": "https://example.org/b"},
                "not a dict",
            ],
            "scholar_papers": [{"title": "Paper", "url": "", "authors": ["X", 3], "year": 0, "abstract": "abs"}],
            "crawled_pages": [{"url": "javascript:alert(1)", "markdown": "x"}],
            "pdf_extractions": [{"url": "https://example.org/p.pdf", "title": None, "abstract": "", "text": "body"}],
        }
    )

    assert [(item.title, item.source_type, item.excerpt) for item in items] == [
        ("Good", "web_search", "snippet"),
        ("Paper", "scholar", "abs"),
        ("https://example.org/p.pdf", "pdf_extract", "body"),
    ]
    assert items[1].url is None and items[1].authors == ["X"] and items[1].year is None
    assert _normalize({"web_results": "nope"}) == []


def test_production_requires_https_and_token_when_retrieval_enabled():
    production = dict(
        environment="production",
        session_secret="x" * 40,
        database_url="postgresql+psycopg://user:pass@db/research",
        auto_create_schema=False,
        public_origin="https://research.example",
        google_client_id="id",
        google_client_secret="secret",
        google_redirect_uri="https://research.example/api/auth/callback",
    )
    assert Settings(**production).arc_retrieval_enabled is False

    with pytest.raises(ValueError, match="ARC_RETRIEVAL_BASE_URL"):
        Settings(**production, arc_retrieval_enabled=True, arc_retrieval_base_url="http://arc.internal")
    with pytest.raises(ValueError, match="ARC_RETRIEVAL_TOKEN"):
        Settings(**production, arc_retrieval_enabled=True, arc_retrieval_base_url="https://arc.internal")
    assert Settings(
        **production, arc_retrieval_enabled=True, arc_retrieval_base_url="https://arc.internal", arc_retrieval_token="t" * 16
    ).arc_retrieval_enabled


def test_every_normalized_source_type_counts_as_automated():
    data = {
        "web_results": [{"title": "W", "url": "https://w.example"}],
        "scholar_papers": [{"title": "S"}],
        "crawled_pages": [{"url": "https://c.example"}],
        "pdf_extractions": [{"url": "https://p.example/a.pdf"}],
    }
    emitted = {item.source_type for item in _normalize(data)}
    # One item per ARC result kind; a new kind must be added to ARC_SOURCE_TYPES too.
    assert len(emitted) == 4
    assert emitted <= ARC_SOURCE_TYPES


def test_source_api_reports_is_automated_until_verified():
    client, project_id = development_client()
    manual = client.post(f"/api/projects/{project_id}/sources", json={"title": "Manual report"})
    assert manual.json()["is_automated"] is False
    with SessionLocal() as db:
        retrieved = Source(project_id=uuid.UUID(project_id), title="Retrieved", source_type="scholar", origin="retrieved", metadata_verified=False)
        db.add(retrieved)
        db.commit()
        retrieved_id = str(retrieved.id)

    listed = {s["id"]: s["is_automated"] for s in client.get(f"/api/projects/{project_id}/sources").json()}
    assert listed == {manual.json()["id"]: False, retrieved_id: True}
    verified = client.post(f"/api/projects/{project_id}/sources/{retrieved_id}/verify")
    assert verified.status_code == 200 and verified.json()["is_automated"] is False


@pytest.mark.parametrize("source_type", sorted(ARC_SOURCE_TYPES) + [" Scholar "])
def test_manual_sources_cannot_use_retrieval_types(source_type):
    client, project_id = development_client()

    response = client.post(f"/api/projects/{project_id}/sources", json={"title": "Typed", "source_type": source_type})

    assert response.status_code == 422
    assert client.get(f"/api/projects/{project_id}/sources").json() == []
