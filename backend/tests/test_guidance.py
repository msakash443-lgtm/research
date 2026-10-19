import uuid

import pytest
from fastapi.testclient import TestClient

from app import guidance
from app.database import SessionLocal
from app.exit_conditions import RULES, Condition, Level
from app.guidance import GUIDE_PATH, GuideError, load_guide, next_steps
from app.main import app
from app.models import Project, ProjectStage


def _owner_project():
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": "guidance@example.com", "display_name": "G"})
    pid = client.post("/api/projects", json={"title": "Guidance"}).json()["id"]
    return client, pid


def test_the_shipped_guide_loads_and_covers_every_stage_gate_and_condition():
    guide = load_guide()

    assert len(guide.stages) == len(ProjectStage)
    assert {c for c in guide.conditions} >= {rule.id for rule in RULES}


def test_a_guide_missing_a_stage_fails_loudly(tmp_path):
    text = GUIDE_PATH.read_text(encoding="utf-8")
    start = text.index("[stages.accepted]")
    end = text.index("# ---------- Gates")
    broken = tmp_path / "journey.toml"
    broken.write_text(text[:start] + text[end:], encoding="utf-8")

    with pytest.raises(GuideError, match="accepted"):
        guidance._load(str(broken))


def test_an_answer_pointing_to_an_unknown_path_fails_loudly(tmp_path):
    text = GUIDE_PATH.read_text(encoding="utf-8")
    broken = tmp_path / "journey.toml"
    broken.write_text(text.replace('points_to = ["scoping_review"]', 'points_to = ["nope"]', 1), encoding="utf-8")

    with pytest.raises(GuideError, match="nope"):
        guidance._load(str(broken))


def test_built_flags_match_which_conditions_are_not_built_yet():
    _, pid = _owner_project()
    guide = load_guide()

    with SessionLocal() as db:
        project = db.get(Project, uuid.UUID(pid))
        for rule in RULES:
            condition = rule.check(db, project)
            assert guide.conditions[rule.id].built == (not condition.message.startswith("not built yet")), rule.id


def test_next_steps_put_required_first_skip_met_and_not_built_and_list_shared_conditions_once():
    stale = Condition("EC-0.2", Level.required, False, "1 stale result(s)", "Redo list")
    options = [
        (
            ProjectStage.scoped,
            [
                Condition("EC-1.2", Level.recommended, False, "no research question", 'Scope: "Add question"'),
                stale,
                Condition("EC-1.3", Level.information, True, "no captured ideas left", None),
                Condition("EC-10.1", Level.information, False, "not built yet (needs M4.1)", None),
            ],
        ),
        (ProjectStage.search_planned, [stale]),
    ]

    found = next_steps(options, load_guide())

    assert [(s.condition_id, s.level) for s in found] == [("EC-0.2", Level.required), ("EC-1.2", Level.recommended)]
    assert found[0].step == "approvals"
    assert found[0].meaning == load_guide().conditions["EC-0.2"].meaning


def test_an_unmet_condition_the_guide_does_not_describe_is_still_suggested():
    options = [(ProjectStage.scoped, [Condition("EC-99.9", Level.required, False, "forced", None)])]

    found = next_steps(options, load_guide())

    assert [(s.condition_id, s.meaning, s.step) for s in found] == [("EC-99.9", None, None)]


def test_a_new_project_is_told_to_write_a_question_and_get_g1_approved():
    client, pid = _owner_project()

    suggestions = client.get(f"/api/projects/{pid}/stage").json()["suggestions"]

    assert [(s["condition_id"], s["level"], s["step"]) for s in suggestions] == [
        ("EC-1.1", "required", "scope"),
        ("EC-1.2", "recommended", "scope"),
    ]
    assert client.post(f"/api/projects/{pid}/gates/G1/approve", json={}).status_code == 200
    suggestions = client.get(f"/api/projects/{pid}/stage").json()["suggestions"]
    assert [s["condition_id"] for s in suggestions] == ["EC-1.2"]


def test_the_guide_is_served_only_to_a_signed_in_user():
    assert TestClient(app).get("/api/guide").status_code == 401
    client, _ = _owner_project()

    response = client.get("/api/guide")

    assert response.status_code == 200
    body = response.json()
    methods = {m["id"]: m for m in body["synthesis_methods"]}
    assert methods["meta_analysis"]["available"] is False
    assert len(body["stages"]) == 19
