import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.preregistration import FILL, RQ, HypothesisItem, build_preregistration


def test_hypotheses_appear_verbatim_and_unknowns_are_bracketed_never_guessed():
    draft = build_preregistration(
        title="Trust and delivery",
        research_questions=[RQ("Does trust predict delivery speed?", "confirmatory")],
        hypotheses=[HypothesisItem("Trust is positively related to delivery speed.", "positive", "Does trust predict delivery speed?")],
    )
    assert "Trust is positively related to delivery speed." in draft.main_hypothesis
    assert FILL.format("sample size") in draft.sample_size
    assert FILL.format("exclusion criteria") in draft.exclusions
    assert FILL.format("data already collected") in draft.data_collected


def test_no_hypothesis_stored_means_the_hypothesis_question_stays_open():
    draft = build_preregistration(title="Empty", research_questions=[], hypotheses=[])
    assert draft.main_hypothesis == FILL.format("main hypothesis")
    assert draft.hypothesis_count == 0


def test_a_question_without_a_hypothesis_is_listed_for_the_researcher_not_called_confirmatory():
    draft = build_preregistration(
        title="Mixed",
        research_questions=[RQ("What do teams find hard?", "exploratory")],
        hypotheses=[],
    )
    assert draft.main_hypothesis == FILL.format("main hypothesis")
    assert draft.unhypothesised_questions == ["What do teams find hard?"]


def test_the_draft_says_nothing_is_submitted_and_names_the_forms_to_check():
    draft = build_preregistration(title="X", research_questions=[], hypotheses=[])
    assert "not submitted" in draft.note.lower()
    assert "aspredicted" in draft.note.lower() and "osf" in draft.note.lower()


def test_api_returns_the_draft_for_a_project(tmp_path=None):
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": f"prereg-{uuid.uuid4().hex}@example.com", "display_name": "P"})
    pid = client.post("/api/projects", json={"title": "Prereg"}).json()["id"]
    body = client.get(f"/api/projects/{pid}/preregistration").json()
    assert body["gate"] == "G6"
    assert {"aspredicted", "osf"} <= set(body["forms"])
    assert body["aspredicted"]["main_hypothesis"] == FILL.format("main hypothesis")
    assert body["osf"]["sections"]
