import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal
from app.main import app
from app.models import Gap, Hypothesis, ResearchQuestion, ResearchQuestionGap


@pytest.fixture
def world():
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": f"rq-{uuid.uuid4().hex}@example.com", "display_name": "R"})
    project = client.post("/api/projects", json={"title": "Framework drafts"}).json()["id"]
    with SessionLocal() as db:
        gap = Gap(project_id=uuid.UUID(project), type="empirical", statement="A selected evidence-backed gap", selected=True, created_by="test")
        db.add(gap)
        db.commit()
        db.refresh(gap)
        gap_id = str(gap.id)
    return client, project, gap_id


def test_question_hypothesis_and_advisory_finer_critique_form_an_editable_human_draft_loop(world):
    client, project_id, gap_id = world
    question = client.post(f"/api/projects/{project_id}/research-questions", json={
        "text": "How does trust affect remote collaboration?", "type": "relational", "gap_ids": [gap_id],
    })
    assert question.status_code == 201, question.text
    question_id = question.json()["id"]
    assert question.json()["gap_ids"] == [gap_id]

    critique = client.get(f"/api/projects/{project_id}/research-questions/{question_id}/finer")
    assert critique.status_code == 200
    assert critique.json()["linked_gap_count"] == 1
    assert {item["criterion"] for item in critique.json()["items"]} == {"feasible", "interesting", "novel", "ethical", "relevant"}
    assert "does not validate scholarship" in critique.json()["disclaimer"]

    assert client.patch(f"/api/projects/{project_id}/research-questions/{question_id}", json={"text": "How does trust shape remote collaboration?"}).status_code == 200
    hypothesis = client.post(f"/api/projects/{project_id}/hypotheses", json={
        "research_question_id": question_id,
        "statement": "Trust is positively associated with remote collaboration quality.",
        "direction": "positive",
        "theoretical_basis": "Social exchange theory describes reciprocal trust.",
        "empirical_support": "The selected literature gap identifies insufficient evidence in remote teams.",
    })
    assert hypothesis.status_code == 201, hypothesis.text
    assert client.patch(f"/api/projects/{project_id}/hypotheses/{hypothesis.json()['id']}", json={"direction": "non_directional"}).json()["direction"] == "non_directional"


def test_question_requires_selected_project_gap_and_hypothesis_requires_project_question(world):
    client, project_id, gap_id = world
    assert client.post(f"/api/projects/{project_id}/research-questions", json={
        "text": "Invalid", "type": "exploratory", "gap_ids": [str(uuid.uuid4())],
    }).status_code == 422

    other = client.post("/api/projects", json={"title": "Other framework"}).json()["id"]
    with SessionLocal() as db:
        foreign_gap = Gap(project_id=uuid.UUID(other), type="empirical", statement="Other selected gap", selected=True, created_by="test")
        db.add(foreign_gap)
        db.commit()
    assert client.post(f"/api/projects/{project_id}/research-questions", json={
        "text": "Cross-project", "type": "exploratory", "gap_ids": [str(foreign_gap.id)],
    }).status_code == 422

    question = client.post(f"/api/projects/{project_id}/research-questions", json={
        "text": "Valid question", "type": "exploratory", "gap_ids": [gap_id],
    }).json()
    assert client.post(f"/api/projects/{other}/hypotheses", json={
        "research_question_id": question["id"], "statement": "Foreign question", "direction": "positive",
        "theoretical_basis": "Basis", "empirical_support": "Support",
    }).status_code == 404


def test_required_draft_fields_reject_whitespace_only_values(world):
    client, project_id, gap_id = world
    assert client.post(f"/api/projects/{project_id}/research-questions", json={
        "text": "   ", "type": "exploratory", "gap_ids": [gap_id],
    }).status_code == 422
    question = client.post(f"/api/projects/{project_id}/research-questions", json={
        "text": "Valid question", "type": "exploratory", "gap_ids": [gap_id],
    }).json()
    assert client.post(f"/api/projects/{project_id}/hypotheses", json={
        "research_question_id": question["id"], "statement": "A statement", "direction": "positive",
        "theoretical_basis": "   ", "empirical_support": "Support",
    }).status_code == 422


def test_database_rejects_cross_project_question_gap_and_hypothesis_links(world):
    client, project_id, _ = world
    other = client.post("/api/projects", json={"title": "Other framework"}).json()["id"]
    with SessionLocal() as db:
        local_question = ResearchQuestion(project_id=uuid.UUID(project_id), text="Local question", type="exploratory", created_by="test")
        foreign_gap = Gap(project_id=uuid.UUID(other), type="empirical", statement="Foreign gap", selected=True, created_by="test")
        db.add_all([local_question, foreign_gap])
        db.flush()
        db.add(ResearchQuestionGap(project_id=uuid.UUID(project_id), research_question_id=local_question.id, gap_id=foreign_gap.id))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

    with SessionLocal() as db:
        local_question = ResearchQuestion(project_id=uuid.UUID(project_id), text="Local hypothesis question", type="exploratory", created_by="test")
        db.add(local_question)
        db.flush()
        db.add(Hypothesis(project_id=uuid.UUID(other), research_question_id=local_question.id, statement="Foreign hypothesis", direction="positive", theoretical_basis="Basis", empirical_support="Support", created_by="test"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
