import uuid

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Project, ProjectStage, User

# Spec Appendix C, in workflow order.
APPENDIX_C = [
    "idea", "scoped", "search_planned", "retrieved", "screened", "extracted", "synthesized", "gaps_selected",
    "framework", "design_approved", "data_collected", "plan_locked", "analyzed", "drafted", "revised",
    "submission_ready", "submitted", "revision_loop", "accepted",
]


def test_stage_enum_matches_appendix_c_in_order():
    assert [s.value for s in ProjectStage] == APPENDIX_C


def test_new_projects_start_at_idea_and_expose_the_stage():
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": "stage@example.com", "display_name": "S"})

    created = client.post("/api/projects", json={"title": "Staged"}).json()

    assert created["stage"] == "idea"
    assert "status" not in created  # the free-text status is retired; `stage` replaces it
    assert client.get(f"/api/projects/{created['id']}").json()["stage"] == "idea"
    assert client.get("/api/projects").json()[0]["stage"] == "idea"


def test_stage_round_trips_every_value():
    with SessionLocal() as db:
        user = User(email=f"stage-{uuid.uuid4().hex}@example.test")
        db.add(user)
        db.flush()
        for stage in ProjectStage:
            db.add(Project(owner_id=user.id, title=stage.value, stage=stage))
        db.commit()
        stored = {p.title: p.stage for p in db.query(Project).all()}

    assert stored == {s.value: s for s in ProjectStage}
