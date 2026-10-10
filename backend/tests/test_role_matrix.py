"""The per-endpoint role matrix (plan M0.3.4).

Every project-scoped route is listed below with the roles allowed to use it and the
status a permitted call returns. Roles are written out literally on purpose: the matrix
pins the agreed policy, so changing a role set in `dependencies.py` fails here.

Everyone outside the allowed set must be refused: a member with the wrong role gets 403,
a non-member 404 (existence is not revealed), and an anonymous caller 401.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import (
    utcnow,
    SOURCE_ORIGIN_RETRIEVED,
    Cluster,
    ClusterRun,
    Extraction,
    Project,
    ProjectMember,
    ProjectExtractionSchema,
    ProjectRole,
    ProjectStage,
    SearchQuery,
    SeedPaper,
    Source,
)
from tests.test_fulltext import make_pdf

UPLOAD_PDF = make_pdf([["Role matrix upload body text."]])

ALL = {"owner", "co_author", "supervisor", "reviewer"}
WRITE = {"owner", "co_author"}
MANAGE = {"owner"}
APPROVE_G1 = {"owner", "supervisor"}  # gate G1; owner-only gates are in test_gate_decisions.py

# (method, path template, roles allowed, status for an allowed call)
MATRIX = [
    ("GET", "/api/projects/{p}", ALL, 200),
    ("GET", "/api/projects/{p}/context", ALL, 200),
    ("POST", "/api/projects/{p}/context", WRITE, 201),
    ("GET", "/api/projects/{p}/sources", ALL, 200),
    ("GET", "/api/projects/{p}/usage", ALL, 200),
    ("PUT", "/api/projects/{p}/usage/budget", MANAGE, 200),  # owner only: sets this project's own token-budget override
    ("POST", "/api/projects/{p}/searches/suggest-synonyms", WRITE, 200),
    ("POST", "/api/projects/{p}/sources", WRITE, 201),
    ("POST", "/api/projects/{p}/sources/{s}/verify", WRITE, 200),
    ("POST", "/api/projects/{p}/sources/merge", WRITE, 200),
    ("POST", "/api/projects/{p}/sources/import", WRITE, 201),
    ("GET", "/api/projects/{p}/sources/export", ALL, 200),
    ("POST", "/api/projects/{p}/sources/{s}/check", WRITE, 202),
    ("POST", "/api/projects/{p}/sources/{s}/fulltext/fetch", WRITE, 202),  # queues only
    ("POST", "/api/projects/{p}/sources/{s}/fulltext/arxiv/fetch", WRITE, 202),  # queues only
    ("POST", "/api/projects/{p}/sources/{s}/fulltext/upload", WRITE, 201),  # raw PDF body; _call gives each principal a fresh source (write-once)
    ("GET", "/api/projects/{p}/searches", ALL, 200),
    ("GET", "/api/projects/{p}/seeds", ALL, 200),
    ("POST", "/api/projects/{p}/seeds", WRITE, 201),
    ("DELETE", "/api/projects/{p}/seeds/{d}", WRITE, 204),
    ("GET", "/api/projects/{p}/known-items", ALL, 200),
    ("GET", "/api/projects/{p}/recall-report", ALL, 200),
    ("POST", "/api/projects/{p}/searches", WRITE, 202),  # queues only; gate G2 still blocks the run
    ("POST", "/api/projects/{p}/searches/{q}/rerun", WRITE, 202),
    ("GET", "/api/projects/{p}/snowball", ALL, 200),
    ("POST", "/api/projects/{p}/snowball", WRITE, 202),  # queues only; gate G2 still blocks the run
    ("POST", "/api/projects/{p}/searches/{q}/alerts", WRITE, 201),
    ("GET", "/api/projects/{p}/alerts", ALL, 200),
    ("POST", "/api/projects/{p}/alerts/{a}/run", WRITE, 202),  # queues only; gate G2 still blocks the run
    ("DELETE", "/api/projects/{p}/alerts/{a}", WRITE, 204),
    ("GET", "/api/projects/{p}/research-runs", ALL, 200),
    ("POST", "/api/projects/{p}/research-runs", WRITE, 202),
    ("GET", "/api/projects/{p}/members", ALL, 200),
    ("POST", "/api/projects/{p}/members", MANAGE, 201),
    ("DELETE", "/api/projects/{p}/members/{u}", MANAGE, 204),
    ("GET", "/api/projects/{p}/audit", ALL, 200),
    ("GET", "/api/projects/{p}/audit/export", ALL, 200),
    ("GET", "/api/projects/{p}/export/obsidian", ALL, 200),
    ("GET", "/api/projects/{p}/zotero", ALL, 200),
    ("POST", "/api/projects/{p}/zotero/pull", WRITE, 202),  # queues only; gate G2 still blocks the run
    ("POST", "/api/projects/{p}/zotero/push", WRITE, 202),
    ("GET", "/api/projects/{p}/artifacts", ALL, 200),
    ("GET", "/api/projects/{p}/rerun-path", ALL, 200),
    ("GET", "/api/projects/{p}/stage", ALL, 200),
    ("GET", "/api/projects/{p}/profile", ALL, 200),
    ("GET", "/api/projects/{p}/criteria", ALL, 200),
    ("GET", "/api/projects/{p}/prisma", ALL, 200),
    ("GET", "/api/projects/{p}/screening/queue", ALL, 200),
    ("GET", "/api/projects/{p}/screening/decisions", ALL, 200),
    ("POST", "/api/projects/{p}/screening/decisions", WRITE, 201),
    ("POST", "/api/projects/{p}/screening/decisions/undo", WRITE, 201),
    ("POST", "/api/projects/{p}/screening/prescreen", WRITE, 202),  # queues only; gate G2 still blocks the run
    ("GET", "/api/projects/{p}/extractions", ALL, 200),
    ("GET", "/api/projects/{p}/extractions/{e}", ALL, 200),
    ("POST", "/api/projects/{p}/extractions", WRITE, 201),
    ("PUT", "/api/projects/{p}/extractions/{e}", WRITE, 200),
    ("GET", "/api/projects/{p}/extraction-schemas", ALL, 200),
    ("GET", "/api/projects/{p}/extraction-schemas/{n}", ALL, 200),
    ("POST", "/api/projects/{p}/extraction-schemas", WRITE, 201),
    ("PUT", "/api/projects/{p}/extraction-schemas/{n}", WRITE, 200),
    ("GET", "/api/projects/{p}/clusters", ALL, 200),
    ("GET", "/api/projects/{p}/coverage-matrix", ALL, 200),
    ("GET", "/api/projects/{p}/bibliometrics/publication-trends", ALL, 200),
    ("GET", "/api/projects/{p}/export/replication", ALL, 200),
    ("GET", "/api/projects/{p}/clusters/{c}", ALL, 200),
    ("POST", "/api/projects/{p}/clusters", WRITE, 202),  # queues only
    ("PATCH", "/api/projects/{p}/clusters/{c}/clusters/{l}", WRITE, 200),
    ("PUT", "/api/projects/{p}/criteria", WRITE, 200),  # who edits criteria; G2 approval is separate
    ("PUT", "/api/projects/{p}/profile", MANAGE, 200),  # owner only: decides databases and reporting norms
    ("POST", "/api/projects/{p}/stage/advance", WRITE, 200),
    ("POST", "/api/projects/{p}/stage/reenter", MANAGE, 200),  # owner only: it voids approvals and stales work
    ("GET", "/api/projects/{p}/gates", ALL, 200),
    ("POST", "/api/projects/{p}/gates/G1/approve", APPROVE_G1, 200),
    ("POST", "/api/projects/{p}/gates/G1/reject", APPROVE_G1, 200),
    # The project here is past G1's stage, so a permitted caller is told to use re-entry (409); reopening
    # itself is covered in test_gate_reopen.py. This row checks who may get that far.
    ("POST", "/api/projects/{p}/gates/G1/reopen", APPROVE_G1, 409),
]

BODIES = {
    ("PUT", "/api/projects/{p}/usage/budget"): {"override": 5000},
    ("PUT", "/api/projects/{p}/criteria"): {"framework": "custom", "criteria": [{"kind": "include", "text": "Peer-reviewed studies"}]},
    ("PUT", "/api/projects/{p}/profile"): {"discipline": "economics", "overrides": {"databases": ["openalex"]}},
    ("POST", "/api/projects/{p}/stage/reenter"): {"stage": "scoped", "reason": "Rework the scope"},
    ("POST", "/api/projects/{p}/context"): {"kind": "question", "content": "Why?"},
    ("POST", "/api/projects/{p}/sources"): {"title": "A source"},
    ("POST", "/api/projects/{p}/searches/suggest-synonyms"): {"block": {"label": "work", "terms": ["remote work"]}},
    ("POST", "/api/projects/{p}/sources/import"): {"format": "bibtex", "text": "@article{k, title={Imported}}"},
    ("POST", "/api/projects/{p}/research-runs"): {"question": "A long enough question?"},
    ("POST", "/api/projects/{p}/seeds"): {"title": "Another seed paper", "doi": "10.1234/seedx"},
    ("POST", "/api/projects/{p}/searches"): {"database": "openalex", "blocks": [{"label": "a", "terms": ["remote work"]}]},
    ("POST", "/api/projects/{p}/snowball"): {"connector": "openalex", "rounds": 1},
    ("POST", "/api/projects/{p}/gates/G1/reject"): {"note": "Needs a clearer scope"},
    ("POST", "/api/projects/{p}/gates/G1/reopen"): {"reason": "Scope needs another look"},
    ("POST", "/api/projects/{p}/clusters"): {"k": 2},
    ("PATCH", "/api/projects/{p}/clusters/{c}/clusters/{l}"): {"label": "Remote work and wellbeing"},
    ("POST", "/api/projects/{p}/extraction-schemas"): {"name": "copied_schema", "based_on": "default"},
    ("PUT", "/api/projects/{p}/extraction-schemas/{n}"): {"label": "Lab schema", "description": "Edited", "fields": [{"key": "sample_size", "type": "integer", "description": "N"}]},
    ("PUT", "/api/projects/{p}/extractions/{e}"): {"fields": {"research_question": "Why?"}, "evidence": {"research_question": {"quote": "We ask why", "page": 1}}},
}

# Principals: the four roles (the creator is the owner), a second owner added via a member row,
# a user with no membership, and someone not signed in.
PRINCIPALS = ["owner", "owner_member", "co_author", "supervisor", "reviewer", "stranger", "anonymous"]


def _login(email):
    client = TestClient(app)
    user_id = client.post("/api/auth/development/login", json={"email": email, "display_name": "M"}).json()["id"]
    return client, user_id


@pytest.fixture
def world(monkeypatch, fake_llm):
    from app.config import get_settings

    # Only the synonym-suggestion route calls a model here; give it a well-formed reply.
    fake_llm.handler = lambda r: {"choices": [{"message": {"content": '{"suggestions": []}'}}]}

    monkeypatch.setattr(get_settings(), "connectors_enabled", ["openalex", "unpaywall", "arxiv"])
    monkeypatch.setattr(get_settings(), "connector_contact_email", "matrix@example.com")
    monkeypatch.setattr(get_settings(), "obsidian_export_enabled", True)
    monkeypatch.setattr(get_settings(), "zotero_sync_enabled", True)
    from pydantic import SecretStr
    monkeypatch.setattr(get_settings(), "zotero_api_key", SecretStr("matrix-key"))
    monkeypatch.setattr(get_settings(), "zotero_user_id", "12345")
    monkeypatch.setattr(get_settings(), "embedding_model", "test-embedding")  # only POST /clusters checks it; nothing is embedded here
    tag = uuid.uuid4().hex[:8]
    owner, _ = _login(f"matrix-owner-{tag}@example.com")
    project_id = owner.post("/api/projects", json={"title": "Matrix"}).json()["id"]
    source_id = owner.post(f"/api/projects/{project_id}/sources", json={"title": "Seed", "doi": "10.1234/seed"}).json()["id"]
    twin_id = owner.post(f"/api/projects/{project_id}/sources", json={"title": "Seed (copy)", "doi": "10.1234/seed"}).json()["id"]
    with SessionLocal() as db:  # past the first stage, so there is somewhere earlier to re-enter
        db.get(Project, uuid.UUID(project_id)).stage = ProjectStage.search_planned  # next stage (retrieved) has no gate
        db.commit()

    clients = {"owner": owner, "anonymous": TestClient(app), "stranger": _login(f"matrix-stranger-{tag}@example.com")[0]}
    for name, role in (
        ("owner_member", ProjectRole.owner),
        ("co_author", ProjectRole.co_author),
        ("supervisor", ProjectRole.supervisor),
        ("reviewer", ProjectRole.reviewer),
    ):
        client, user_id = _login(f"matrix-{name}-{tag}@example.com")
        with SessionLocal() as db:
            db.add(ProjectMember(project_id=uuid.UUID(project_id), user_id=uuid.UUID(user_id), role=role))
            db.commit()
        clients[name] = client

    with SessionLocal() as db:  # an earlier run of a search, so it can be re-run and watched
        from app.models import SearchAlert

        search_id = uuid.uuid4()
        db.add(SearchQuery(
            project_id=uuid.UUID(project_id), search_id=search_id, database="openalex", query_string='"x"',
            run_at=utcnow(), n_results=1, counts={"retrieved": 1, "unique": 1},
            results=[{"id": "W1", "doi": "10.1000/matrix", "work_key": "matrix-paper"}],
        ))
        # A second, already-run search that already has an alert: the target of the run/delete routes.
        alerted_search_id = uuid.uuid4()
        db.add(SearchQuery(
            project_id=uuid.UUID(project_id), search_id=alerted_search_id, database="openalex", query_string='"y"',
            run_at=utcnow(), n_results=1, counts={"retrieved": 1, "unique": 1},
            results=[{"id": "W2", "doi": "10.1000/matrix-2", "work_key": "matrix-paper-2"}],
        ))
        alert_id = uuid.uuid4()
        db.add(SearchAlert(
            id=alert_id,
            project_id=uuid.UUID(project_id), search_id=alerted_search_id, interval_seconds=86400,
            next_run_at=utcnow(), enabled=True, created_by="matrix",
        ))
        db.commit()

    with SessionLocal() as db:
        seed_id = uuid.uuid4()
        db.add(SeedPaper(id=seed_id, project_id=uuid.UUID(project_id), title="A seed paper title"))
        db.commit()

    with SessionLocal() as db:  # an unverified extraction to read and edit
        extraction_id = uuid.uuid4()
        db.add(Extraction(id=extraction_id, project_id=uuid.UUID(project_id), source_id=uuid.UUID(source_id), schema_version="default@1",
                          fields_json={}, evidence_spans={}, extracted_by="human", extractor="seed"))
        db.commit()

    with SessionLocal() as db:  # a project extraction schema to read and edit
        db.add(ProjectExtractionSchema(project_id=uuid.UUID(project_id), name="lab_schema", version=1, label="Lab", description="",
                                       fields=[{"key": "sample_size", "type": "integer", "description": "N", "critical": False}],
                                       based_on="default@1", created_by="seed"))
        db.commit()

    with SessionLocal() as db:  # a stored clustering with one cluster to read and rename
        clustering_id, cluster_id = uuid.uuid4(), uuid.uuid4()
        db.add(ClusterRun(id=clustering_id, project_id=uuid.UUID(project_id), model_id="test-embedding", k=2, seed=0, n_sources=2,
                          created_by="seed", clusters=[Cluster(id=cluster_id, position=1, label="Cluster 1")]))
        db.commit()

    # Targets for the member routes: an uninvited user to add, and a removable reviewer.
    _login(f"matrix-invitee-{tag}@example.com")
    _, removable_id = _login(f"matrix-removable-{tag}@example.com")
    with SessionLocal() as db:
        db.add(ProjectMember(project_id=uuid.UUID(project_id), user_id=uuid.UUID(removable_id), role=ProjectRole.reviewer))
        db.commit()
    return {
        "clients": clients,
        "ids": {"p": project_id, "s": source_id, "u": removable_id, "q": str(search_id), "d": str(seed_id), "e": str(extraction_id), "c": str(clustering_id), "l": str(cluster_id), "n": "lab_schema", "a": str(alert_id)},
        "twin": twin_id,
        "invitee": f"matrix-invitee-{tag}@example.com",
    }


def _call(world, principal, method, template):
    path = template.format(**world["ids"])
    body = BODIES.get((method, template))
    if (method, template) == ("POST", "/api/projects/{p}/members"):
        body = {"email": world["invitee"], "role": "reviewer"}
    if (method, template) == ("POST", "/api/projects/{p}/sources/merge"):
        body = {"source_ids": [world["ids"]["s"], world["twin"]]}
    if (method, template) == ("POST", "/api/projects/{p}/screening/decisions"):
        body = {"source_id": world["ids"]["s"], "decision": "maybe"}
    if (method, template) == ("POST", "/api/projects/{p}/extractions"):
        body = {"source_id": world["ids"]["s"], "fields": {"research_question": None}}
    if (method, template) == ("POST", "/api/projects/{p}/screening/decisions/undo"):
        body = {"source_id": world["ids"]["s"]}
        # there must be a decision to take back; the owner records it, whoever is being tested
        owner_body = {"source_id": world["ids"]["s"], "decision": "maybe"}
        world["clients"]["owner"].post(f"/api/projects/{world['ids']['p']}/screening/decisions", json=owner_body)
    if (method, template) == ("POST", "/api/projects/{p}/sources/{s}/fulltext/upload"):
        # Upload is write-once per source: give every principal under test a fresh, never-uploaded
        # source so a successful call from one principal doesn't 409 the next.
        fresh = world["clients"]["owner"].post(
            f"/api/projects/{world['ids']['p']}/sources", json={"title": "Upload target", "evidence_excerpt": "x"}
        ).json()
        path = path.replace(f"/sources/{world['ids']['s']}/", f"/sources/{fresh['id']}/")
        return world["clients"][principal].post(path, content=UPLOAD_PDF, headers={"content-type": "application/pdf"})
    if (method, template) == ("POST", "/api/projects/{p}/sources/{s}/fulltext/arxiv/fetch"):
        fresh = world["clients"]["owner"].post(
            f"/api/projects/{world['ids']['p']}/sources", json={"title": "arXiv fetch target"}
        ).json()
        with SessionLocal() as db:
            source = db.get(Source, uuid.UUID(fresh["id"]))
            source.origin = SOURCE_ORIGIN_RETRIEVED
            source.source_ids = {"arxiv": "2101.00001"}
            db.commit()
        path = path.replace(f"/sources/{world['ids']['s']}/", f"/sources/{fresh['id']}/")
        return world["clients"][principal].post(path)
    return world["clients"][principal].request(method, path, json=body)


@pytest.mark.parametrize("principal", PRINCIPALS)
@pytest.mark.parametrize("method,template,allowed,success", MATRIX, ids=[f"{m} {t}" for m, t, _, _ in MATRIX])
def test_role_matrix(world, principal, method, template, allowed, success):
    response = _call(world, principal, method, template)

    if principal == "anonymous":
        assert response.status_code == 401
    elif principal == "stranger":
        assert response.status_code == 404
    elif principal.removesuffix("_member") in allowed:
        assert response.status_code == success, response.text
    else:
        assert response.status_code == 403, response.text


def test_the_matrix_covers_every_project_scoped_route():
    """A new project route must be added to MATRIX, or this fails."""
    routed = {
        (method, route.path.replace("{project_id}", "{p}").replace("{source_id}", "{s}").replace("{user_id}", "{u}").replace("{code}", "G1").replace("{search_id}", "{q}").replace("{seed_id}", "{d}").replace("{extraction_id}", "{e}").replace("{clustering_id}", "{c}").replace("{cluster_id}", "{l}").replace("{schema_name}", "{n}").replace("{alert_id}", "{a}"))
        for route in app.routes
        if "{project_id}" in getattr(route, "path", "")
        for method in route.methods
    }
    covered = {(method, template) for method, template, _, _ in MATRIX}

    assert routed == covered


def test_denied_calls_change_nothing(world):
    """A refused write must not have side effects (checked on the cheapest write routes)."""
    p = world["ids"]["p"]
    reviewer = world["clients"]["reviewer"]
    before = world["clients"]["owner"].get(f"/api/projects/{p}/sources").json()

    assert reviewer.post(f"/api/projects/{p}/sources", json={"title": "Sneaky"}).status_code == 403
    assert reviewer.post(f"/api/projects/{p}/sources/{world['ids']['s']}/verify").status_code == 403

    after = world["clients"]["owner"].get(f"/api/projects/{p}/sources").json()
    assert after == before and after[0]["metadata_verified"] is False
    actions = {e["action"] for e in world["clients"]["owner"].get(f"/api/projects/{p}/audit").json()}
    assert "source.verified" not in actions
