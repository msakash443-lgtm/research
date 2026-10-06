import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.criteria import FRAMEWORKS
from app.database import SessionLocal
from app.main import app
from app.models import AuditEvent, ProjectMember, ProjectRole, ScreeningCriterion


def _login(email):
    client = TestClient(app)
    user_id = client.post("/api/auth/development/login", json={"email": email, "display_name": "C"}).json()["id"]
    return client, user_id


@pytest.fixture
def world():
    tag = uuid.uuid4().hex[:8]
    owner, _ = _login(f"crit-owner-{tag}@example.com")
    pid = owner.post("/api/projects", json={"title": "Criteria"}).json()["id"]
    others = {}
    for role in ("co_author", "supervisor", "reviewer"):
        client, uid = _login(f"crit-{role}-{tag}@example.com")
        with SessionLocal() as db:
            db.add(ProjectMember(project_id=uuid.UUID(pid), user_id=uuid.UUID(uid), role=ProjectRole(role)))
            db.commit()
        others[role] = client
    return {"owner": owner, "pid": pid, **others}


def put(client, pid, body):
    return client.put(f"/api/projects/{pid}/criteria", json=body)


def item(kind="include", text="Peer-reviewed empirical studies", **extra):
    return {"kind": kind, "text": text, **extra}


def test_new_project_has_no_criteria_and_says_what_is_missing(world):
    data = world["owner"].get(f"/api/projects/{world['pid']}/criteria").json()
    assert data["framework"] is None and data["criteria"] == [] and data["locked"] is False
    assert any("framework" in p for p in data["problems"])


def test_save_assigns_stable_codes_per_kind_in_order(world):
    body = {"framework": "custom", "criteria": [item("include", "A study"), item("exclude", "Opinion pieces"), item("include", "Post-2010")]}
    data = put(world["owner"], world["pid"], body).json()
    assert [(c["code"], c["kind"]) for c in data["criteria"]] == [("I1", "include"), ("E1", "exclude"), ("I2", "include")]
    assert data["framework"] == "custom" and data["problems"] == []


@pytest.mark.parametrize("framework", ["pico", "picoc", "spider"])
def test_framework_elements_are_validated_and_coverage_is_reported(world, framework):
    first = FRAMEWORKS[framework][0]
    data = put(world["owner"], world["pid"], {"framework": framework, "criteria": [item(element=first), item("exclude", "Case reports")]}).json()
    assert data["elements"] == list(FRAMEWORKS[framework])
    missing = next(p for p in data["problems"] if p.startswith("No criterion yet for"))
    assert first not in missing and FRAMEWORKS[framework][1] in missing


def test_an_element_from_another_framework_is_refused(world):
    bad = {"framework": "pico", "criteria": [item(element="phenomenon_of_interest")]}
    assert put(world["owner"], world["pid"], bad).status_code == 422
    assert put(world["owner"], world["pid"], {"framework": "custom", "criteria": [item(element="population")]}).status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"framework": "prisma", "criteria": []},
        {"framework": "custom", "criteria": [item("maybe")]},
        {"framework": "custom", "criteria": [item(text="  ")]},
        {"framework": "custom", "criteria": [item(text="x" * 1001)]},
        {"framework": "custom", "criteria": [item(), item()]},  # same criterion twice
        {"framework": "custom", "criteria": [item(code="E1")]},  # code prefix must match kind
        {"framework": "custom", "criteria": [item(code="I1"), item(text="Other", code="I1")]},
        {"framework": "custom", "criteria": [item(code="X9")]},
        {"framework": "custom", "criteria": [item(unknown=1)]},
        {"framework": "custom", "criteria": [item(text=f"Criterion number {i}") for i in range(51)]},
    ],
)
def test_invalid_payloads_are_refused_and_change_nothing(world, body):
    put(world["owner"], world["pid"], {"framework": "custom", "criteria": [item()]})
    assert put(world["owner"], world["pid"], body).status_code == 422
    data = world["owner"].get(f"/api/projects/{world['pid']}/criteria").json()
    assert [c["code"] for c in data["criteria"]] == ["I1"]


def test_text_is_whitespace_normalised(world):
    data = put(world["owner"], world["pid"], {"framework": "custom", "criteria": [item(text="  Adults   over\t18 ")]}).json()
    assert data["criteria"][0]["text"] == "Adults over 18"


def test_kept_codes_stay_and_new_ones_never_reuse_a_dropped_code(world):
    pid = world["pid"]
    put(world["owner"], pid, {"framework": "custom", "criteria": [item("include", "One"), item("include", "Two"), item("include", "Three")]})
    # Drop I2 and add a new criterion: it must not become "I2" (decisions citing I2 would change meaning).
    data = put(world["owner"], pid, {"framework": "custom", "criteria": [
        item("include", "One", code="I1"), item("include", "Three", code="I3"), item("include", "Brand new"),
    ]}).json()
    assert [(c["code"], c["text"]) for c in data["criteria"]] == [("I1", "One"), ("I3", "Three"), ("I4", "Brand new")]


def test_changing_a_criterion_to_the_other_kind_means_a_new_code(world):
    pid = world["pid"]
    put(world["owner"], pid, {"framework": "custom", "criteria": [item("include", "One")]})
    assert put(world["owner"], pid, {"framework": "custom", "criteria": [item("exclude", "One", code="E1")]}).status_code == 200  # new code
    assert world["owner"].get(f"/api/projects/{pid}/criteria").json()["criteria"][0]["code"] == "E1"


def test_who_first_wrote_a_kept_criterion_is_preserved(world):
    pid = world["pid"]
    put(world["owner"], pid, {"framework": "custom", "criteria": [item("include", "Owner wrote this")]})
    data = put(world["co_author"], pid, {"framework": "custom", "criteria": [
        item("include", "Owner wrote this", code="I1"), item("exclude", "Co-author wrote this"),
    ]}).json()
    owner_actor, coauthor_actor = (c["created_by"] for c in data["criteria"])
    assert owner_actor != coauthor_actor and owner_actor and coauthor_actor


def test_roles_read_by_all_write_by_owner_and_co_author_only(world):
    pid = world["pid"]
    body = {"framework": "custom", "criteria": [item()]}
    for role in ("owner", "co_author"):
        assert put(world[role], pid, body).status_code == 200
    for role in ("supervisor", "reviewer"):
        assert put(world[role], pid, body).status_code == 403
        assert world[role].get(f"/api/projects/{pid}/criteria").status_code == 200


def test_approved_g2_locks_the_criteria_and_reopening_unlocks(world):
    pid = world["pid"]
    put(world["owner"], pid, {"framework": "custom", "criteria": [item("include", "Original")]})
    assert world["supervisor"].post(f"/api/projects/{pid}/gates/G2/approve").status_code == 200

    assert world["owner"].get(f"/api/projects/{pid}/criteria").json()["locked"] is True
    response = put(world["owner"], pid, {"framework": "custom", "criteria": [item("include", "Changed after approval")]})
    assert response.status_code == 409 and "G2" in response.json()["detail"]
    assert world["owner"].get(f"/api/projects/{pid}/criteria").json()["criteria"][0]["text"] == "Original"

    # A re-entry voids G2 (M0.5.5); the criteria can then change and must be approved again.
    with SessionLocal() as db:
        from app.models import Gate, GateCode, GateStatus

        gate = db.scalar(select(Gate).where(Gate.project_id == uuid.UUID(pid), Gate.code == GateCode.G2))
        gate.status = GateStatus.pending
        db.commit()
    assert put(world["owner"], pid, {"framework": "custom", "criteria": [item("include", "Changed")]}).status_code == 200


def test_a_rejected_or_pending_g2_does_not_lock(world):
    pid = world["pid"]
    put(world["owner"], pid, {"framework": "custom", "criteria": [item()]})
    assert world["supervisor"].post(f"/api/projects/{pid}/gates/G2/reject", json={"note": "Needs exclusions"}).status_code == 200
    assert world["owner"].get(f"/api/projects/{pid}/criteria").json()["locked"] is False
    assert put(world["owner"], pid, {"framework": "custom", "criteria": [item(), item("exclude", "Case reports")]}).status_code == 200


def test_changes_are_audited_with_codes_not_text(world):
    pid = world["pid"]
    put(world["owner"], pid, {"framework": "custom", "criteria": [item(text="Confidential wording here")]})
    with SessionLocal() as db:
        event = db.scalar(select(AuditEvent).where(AuditEvent.project_id == uuid.UUID(pid), AuditEvent.action == "criteria.updated"))
    assert event.payload_json == {"framework": "custom", "codes_before": [], "codes_after": ["I1"]}
    assert "Confidential" not in str(event.payload_json)


def test_refused_save_writes_no_audit_event(world):
    pid = world["pid"]
    put(world["owner"], pid, {"framework": "nope", "criteria": []})
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.project_id == uuid.UUID(pid), AuditEvent.action == "criteria.updated")) == 0


def test_criteria_are_scoped_to_their_project(world):
    other = world["owner"].post("/api/projects", json={"title": "Other"}).json()["id"]
    put(world["owner"], world["pid"], {"framework": "custom", "criteria": [item()]})
    assert world["owner"].get(f"/api/projects/{other}/criteria").json()["criteria"] == []
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ScreeningCriterion).where(ScreeningCriterion.project_id == uuid.UUID(other))) == 0


def test_outsiders_get_404_and_anonymous_401(world):
    stranger, _ = _login(f"crit-stranger-{uuid.uuid4().hex[:6]}@example.com")
    assert stranger.get(f"/api/projects/{world['pid']}/criteria").status_code == 404
    assert TestClient(app).get(f"/api/projects/{world['pid']}/criteria").status_code == 401
