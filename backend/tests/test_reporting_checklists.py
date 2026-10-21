import uuid

from fastapi.testclient import TestClient

from app import reporting_checklists
from app.database import SessionLocal
from app.main import app
from app.models import Project


def _login(email):
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": email, "display_name": "C"})
    return client


def _project(discipline=None):
    tag = uuid.uuid4().hex[:8]
    owner = _login(f"checklist-{tag}@example.com")
    pid = owner.post("/api/projects", json={"title": "Checklist"}).json()["id"]
    if discipline is not None:
        with SessionLocal() as db:
            db.get(Project, uuid.UUID(pid)).discipline = discipline
            db.commit()
    return owner, pid


def test_prisma_2020_has_its_27_items_numbered_in_order_with_unique_ids():
    items = reporting_checklists.checklist_for("PRISMA-2020")
    assert [item.id for item in items] == [str(n) for n in range(1, 28)]
    assert all(item.section and item.topic for item in items)


def test_a_guideline_without_a_written_checklist_returns_none():
    assert reporting_checklists.checklist_for("STROBE") is None
    assert reporting_checklists.checklist_for(None) is None


def test_checklist_endpoint_returns_the_items_of_the_profiles_guideline():
    owner, pid = _project(discipline="social_sciences")
    body = owner.get(f"/api/projects/{pid}/prisma/checklist").json()
    assert body["guideline"] == "PRISMA-2020" and body["shipped"] is True
    assert len(body["items"]) == 27
    assert body["items"][0] == {"id": "1", "section": "Title", "topic": "Identify the report as a systematic review"}


def test_checklist_endpoint_says_not_shipped_when_no_guideline_is_set():
    owner, pid = _project()
    assert owner.get(f"/api/projects/{pid}/prisma/checklist").json() == {"guideline": None, "shipped": False, "items": []}


def test_checklist_endpoint_refuses_outsiders():
    owner, pid = _project()
    outsider = _login(f"checklist-out-{uuid.uuid4().hex[:8]}@example.com")
    assert outsider.get(f"/api/projects/{pid}/prisma/checklist").status_code == 404
