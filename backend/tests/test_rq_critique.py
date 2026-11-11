import uuid

from app import rq_critique
from app.database import SessionLocal
from app.models import Construct, Gap, ScreeningCriterion, Source

from tests.api_helpers import create_project, sign_in


def _checks_for(question_text, **overrides):
    kwargs = dict(
        research_type="quantitative_survey",
        criteria_terms={},
        construct_names=[],
        has_linked_selected_gap_or_verified_source=False,
    )
    kwargs.update(overrides)
    return {check["check"]: check for check in rq_critique.run(question_text, **kwargs)}


def test_a_question_with_no_question_mark_fails_the_question_form_check():
    check = _checks_for("What the effect of leadership style on team performance is")["question_form"]
    assert check["status"] == "warn"
    assert check["reason"]


def test_a_single_interrogative_sentence_passes_the_question_form_check():
    check = _checks_for("What is the effect of leadership style on team performance?")["question_form"]
    assert check["status"] == "pass"
    assert check["reason"]


def test_two_questions_joined_by_and_fail_the_double_barrelled_check():
    check = _checks_for(
        "What is the effect of leadership style on performance and how does culture moderate this relationship?"
    )["double_barrelled"]
    assert check["status"] == "warn"
    assert check["reason"]


def test_a_single_question_passes_the_double_barrelled_check():
    check = _checks_for("What is the effect of leadership style on team performance?")["double_barrelled"]
    assert check["status"] == "pass"
    assert check["reason"]


def test_a_question_missing_saved_criteria_terms_fails_the_framework_elements_check():
    criteria_terms = {
        "population": ["nurses"],
        "intervention": ["training program"],
        "comparison": ["standard care"],
        "outcome": ["burnout score"],
    }
    check = _checks_for(
        "What is the effect of the training program on burnout score?",
        research_type="quantitative_survey", criteria_terms=criteria_terms,
    )["framework_elements"]
    assert check["status"] == "warn"
    assert "population" in check["reason"]


def test_a_question_mentioning_every_saved_criteria_term_passes_the_framework_elements_check():
    criteria_terms = {
        "population": ["nurses"],
        "intervention": ["training program"],
        "comparison": ["standard care"],
        "outcome": ["burnout score"],
    }
    check = _checks_for(
        "Among nurses, what is the effect of the training program compared with standard care on burnout score?",
        research_type="quantitative_survey", criteria_terms=criteria_terms,
    )["framework_elements"]
    assert check["status"] == "pass"
    assert check["reason"]


def test_a_project_with_no_research_type_gets_human_review_not_pass_on_the_framework_elements_check():
    check = _checks_for("What is the effect of X on Y?", research_type="full_journey", criteria_terms={"outcome": ["Y"]})[
        "framework_elements"
    ]
    assert check["status"] == "human_review"
    assert check["reason"]


def test_a_project_with_no_saved_criteria_terms_gets_human_review_not_pass_on_the_framework_elements_check():
    check = _checks_for("What is the effect of X on Y?", research_type="quantitative_survey", criteria_terms={})[
        "framework_elements"
    ]
    assert check["status"] == "human_review"
    assert check["reason"]


def test_a_vague_impact_question_with_no_named_outcome_fails_the_vague_scope_check():
    check = _checks_for("What is the impact of social media on society?")["vague_scope"]
    assert check["status"] == "warn"
    assert check["reason"]


def test_a_vague_impact_question_with_a_named_outcome_term_passes_the_vague_scope_check():
    check = _checks_for(
        "What is the impact of social media on adolescent anxiety scores?",
        criteria_terms={"outcome": ["anxiety scores"]},
    )["vague_scope"]
    assert check["status"] == "pass"
    assert check["reason"]


def test_a_question_spelling_a_registered_construct_differently_fails_the_constructs_check():
    check = _checks_for("How does self efficacy predict performance?", construct_names=["self-efficacy"])["constructs"]
    assert check["status"] == "warn"
    assert check["reason"]


def test_a_question_spelling_a_registered_construct_the_same_way_passes_the_constructs_check():
    check = _checks_for("How does self-efficacy predict performance?", construct_names=["self-efficacy"])["constructs"]
    assert check["status"] == "pass"
    assert check["reason"]


def test_a_project_with_no_construct_registry_loaded_gets_human_review_not_pass_on_the_constructs_check():
    check = _checks_for("How does self-efficacy predict performance?", construct_names=None)["constructs"]
    assert check["status"] == "human_review"
    assert check["reason"]


def test_a_question_with_no_linked_gap_and_no_verified_source_fails_the_answerability_check():
    check = _checks_for("What is the effect of X on Y?", has_linked_selected_gap_or_verified_source=False)["answerability"]
    assert check["status"] == "warn"
    assert check["reason"]


def test_a_question_with_a_linked_selected_gap_or_verified_source_passes_the_answerability_check():
    check = _checks_for("What is the effect of X on Y?", has_linked_selected_gap_or_verified_source=True)["answerability"]
    assert check["status"] == "pass"
    assert check["reason"]


def test_the_finer_critique_response_merges_rq_checks_alongside_the_original_finer_items():
    client = sign_in(f"rq-critique-{uuid.uuid4().hex}@example.com")
    project_id = create_project(client, title="Critique project")
    assert client.put(f"/api/projects/{project_id}/research-type", json={"name": "quantitative_survey"}).status_code == 200
    put_criteria = client.put(f"/api/projects/{project_id}/criteria", json={
        "framework": "pico",
        "criteria": [
            {"kind": "include", "text": "Adult participants", "element": "population"},
            {"kind": "include", "text": "Receiving the training program", "element": "intervention"},
            {"kind": "include", "text": "Compared with standard care", "element": "comparison"},
            {"kind": "include", "text": "Burnout score measured", "element": "outcome"},
            {"kind": "exclude", "text": "Non-English reports"},
        ],
    })
    assert put_criteria.status_code == 200, put_criteria.text

    with SessionLocal() as db:
        gap = Gap(project_id=uuid.UUID(project_id), type="empirical", statement="A selected evidence-backed gap",
                   selected=True, created_by="test")
        db.add(gap)
        db.add(Construct(project_id=uuid.UUID(project_id), name="burnout", definition="exhaustion from chronic stress",
                          author="test", created_by="test"))
        db.commit()
        db.refresh(gap)
        gap_id = str(gap.id)

    question = client.post(f"/api/projects/{project_id}/research-questions", json={
        "text": "What is the effect of the training program on burnout score?",
        "type": "relational",
        "gap_ids": [gap_id],
    })
    assert question.status_code == 201, question.text
    question_id = question.json()["id"]

    critique = client.get(f"/api/projects/{project_id}/research-questions/{question_id}/finer")
    assert critique.status_code == 200, critique.text
    body = critique.json()
    assert {item["criterion"] for item in body["items"]} == {"feasible", "interesting", "novel", "ethical", "relevant"}
    assert "does not validate scholarship" in body["disclaimer"]
    assert {check["check"] for check in body["rq_checks"]} == {
        "question_form", "double_barrelled", "framework_elements", "vague_scope", "constructs", "answerability",
    }
    checks = {check["check"]: check["status"] for check in body["rq_checks"]}
    assert checks["answerability"] == "pass"
