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
from app.models import Project, ProjectMember, ProjectRole, ProjectStage, SearchQuery, SeedPaper

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
    ("POST", "/api/projects/{p}/sources", WRITE, 201),
    ("POST", "/api/projects/{p}/sources/{s}/verify", WRITE, 200),
    ("POST", "/api/projects/{p}/sources/merge", WRITE, 200),
    ("POST", "/api/projects/{p}/sources/{s}/check", WRITE, 202),
    ("GET", "/api/projects/{p}/searches", ALL, 200),
    ("GET", "/api/projects/{p}/seeds", ALL, 200),
    ("POST", "/api/projects/{p}/seeds", WRITE, 201),
    ("DELETE", "/api/projects/{p}/seeds/{d}", WRITE, 204),
    ("GET", "/api/projects/{p}/known-items", ALL, 200),
    ("POST", "/api/projects/{p}/searches", WRITE, 202),  # queues only; gate G2 still blocks the run
    ("POST", "/api/projects/{p}/searches/{q}/rerun", WRITE, 202),
    ("GET", "/api/projects/{p}/research-runs", ALL, 200),
    ("POST", "/api/projects/{p}/research-runs", WRITE, 202),
    ("GET", "/api/projects/{p}/members", ALL, 200),
    ("POST", "/api/projects/{p}/members", MANAGE, 201),
    ("DELETE", "/api/projects/{p}/members/{u}", MANAGE, 204),
    ("GET", "/api/projects/{p}/audit", ALL, 200),
    ("GET", "/api/projects/{p}/audit/export", ALL, 200),
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
    ("PUT", "/api/projects/{p}/criteria", WRITE, 200),  # who edits criteria; G2 approval is separate
    ("PUT", "/api/projects/{p}/profile", MANAGE, 200),  # owner only: decides databases and reporting norms
    ("POST", "/api/projects/{p}/stage/advance", WRITE, 200),
    ("POST", "/api/projects/{p}/stage/reenter", MANAGE, 200),  # owner only: it voids approvals and stales work
    ("GET", "/api/projects/{p}/gates", ALL, 200),
    ("POST", "/api/projects/{p}/gates/G1/approve", APPROVE_G1, 200),
    ("POST", "/api/projects/{p}/gates/G1/reject", APPROVE_G1, 200),
]

BODIES = {
    ("PUT", "/api/projects/{p}/criteria"): {"framework": "custom", "criteria": [{"kind": "include", "text": "Peer-reviewed studies"}]},
    ("PUT", "/api/projects/{p}/profile"): {"discipline": "economics", "overrides": {"databases": ["openalex"]}},
    ("POST", "/api/projects/{p}/stage/reenter"): {"stage": "scoped", "reason": "Rework the scope"},
    ("POST", "/api/projects/{p}/context"): {"kind": "question", "content": "Why?"},
    ("POST", "/api/projects/{p}/sources"): {"title": "A source"},
    ("POST", "/api/projects/{p}/research-runs"): {"question": "A long enough question?"},
    ("POST", "/api/projects/{p}/seeds"): {"title": "Another seed paper", "doi": "10.1234/seedx"},
    ("POST", "/api/projects/{p}/searches"): {"database": "openalex", "blocks": [{"label": "a", "terms": ["remote work"]}]},
    ("POST", "/api/projects/{p}/gates/G1/reject"): {"note": "Needs a clearer scope"},
}

# Principals: the four roles (the creator is the owner), a second owner added via a member row,
# a user with no membership, and someone not signed in.
PRINCIPALS = ["owner", "owner_member", "co_author", "supervisor", "reviewer", "stranger", "anonymous"]


def _login(email):
    client = TestClient(app)
    user_id = client.post("/api/auth/development/login", json={"email": email, "display_name": "M"}).json()["id"]
    return client, user_id


@pytest.fixture
def world(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "connectors_enabled", ["openalex"])
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

    with SessionLocal() as db:  # an earlier run of a search, so it can be re-run
        search_id = uuid.uuid4()
        db.add(SearchQuery(project_id=uuid.UUID(project_id), search_id=search_id, database="openalex", query_string='"x"'))
        db.commit()

    with SessionLocal() as db:
        seed_id = uuid.uuid4()
        db.add(SeedPaper(id=seed_id, project_id=uuid.UUID(project_id), title="A seed paper title"))
        db.commit()

    # Targets for the member routes: an uninvited user to add, and a removable reviewer.
    _login(f"matrix-invitee-{tag}@example.com")
    _, removable_id = _login(f"matrix-removable-{tag}@example.com")
    with SessionLocal() as db:
        db.add(ProjectMember(project_id=uuid.UUID(project_id), user_id=uuid.UUID(removable_id), role=ProjectRole.reviewer))
        db.commit()
    return {
        "clients": clients,
        "ids": {"p": project_id, "s": source_id, "u": removable_id, "q": str(search_id), "d": str(seed_id)},
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
    if (method, template) == ("POST", "/api/projects/{p}/screening/decisions/undo"):
        body = {"source_id": world["ids"]["s"]}
        # there must be a decision to take back; the owner records it, whoever is being tested
        owner_body = {"source_id": world["ids"]["s"], "decision": "maybe"}
        world["clients"]["owner"].post(f"/api/projects/{world['ids']['p']}/screening/decisions", json=owner_body)
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
        (method, route.path.replace("{project_id}", "{p}").replace("{source_id}", "{s}").replace("{user_id}", "{u}").replace("{code}", "G1").replace("{search_id}", "{q}").replace("{seed_id}", "{d}"))
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
