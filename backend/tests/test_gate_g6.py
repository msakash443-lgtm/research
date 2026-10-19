"""Gate G6: approve research questions, hypotheses and the conceptual model before design work starts
(M4.8). Covers the real EC-8.2/EC-8.3 checks that now block G6's approval (`app/exit_conditions.py`,
`routers/gates.py::_OWN_GATE_ROW`).
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app import exit_conditions
from app.database import SessionLocal
from app.main import app
from app.models import Gap, Project, ProjectStage, Source
from gate_helpers import approve_earlier_gates


def _login(email):
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": email, "display_name": "G"})
    return client


def _project(client, title="Framework gate project"):
    return client.post("/api/projects", json={"title": title}).json()["id"]


def _prep_stage(project_id: str) -> None:
    """Put the project at gaps_selected with G1-G5 approved, G6 left for the test to decide."""
    with SessionLocal() as db:
        project = db.get(Project, uuid.UUID(str(project_id)))
        project.stage = ProjectStage.gaps_selected
        db.commit()
    approve_earlier_gates(project_id, "G6")


def _selected_gap(project_id: str) -> str:
    with SessionLocal() as db:
        gap = Gap(project_id=uuid.UUID(str(project_id)), type="empirical", statement="A selected evidence-backed gap",
                   selected=True, created_by="test")
        db.add(gap)
        db.commit()
        return str(gap.id)


def _linked_research_question(client, project_id, gap_id) -> None:
    response = client.post(f"/api/projects/{project_id}/research-questions", json={
        "text": "How does trust affect remote collaboration?", "type": "relational", "gap_ids": [gap_id],
    })
    assert response.status_code == 201, response.text


def _construct(client, project_id, name, measures=()):
    response = client.post(f"/api/projects/{project_id}/constructs", json={
        "name": name, "definition": "Definition", "author": "Smith", "measures": list(measures)})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _project_source(project_id: str) -> str:
    """Insert a source directly before G1-G5 are approved: G4's approval locks `Source` creation
    (`app.source_gate_guard`), so the conceptual model's backing source must exist first."""
    with SessionLocal() as db:
        source = Source(project_id=uuid.UUID(str(project_id)), title="Smith 2020", source_type="article", created_by="test")
        db.add(source)
        db.commit()
        return str(source.id)


def _conceptual_path(client, project_id, source_id, *, from_measures, to_measures) -> None:
    from_id = _construct(client, project_id, "Trust", from_measures)
    to_id = _construct(client, project_id, "Performance", to_measures)
    response = client.post(f"/api/projects/{project_id}/conceptual-model/paths", json={
        "from_construct_id": from_id, "to_construct_id": to_id, "kind": "direct",
        "source_ids": [source_id], "rationale": "Prior studies report it.",
    })
    assert response.status_code == 201, response.text


def _aligned_conceptual_model(client, project_id, source_id) -> None:
    """A direct path whose two constructs both carry a recorded measure (EC-8.3)."""
    _conceptual_path(client, project_id, source_id, from_measures=[{"scale": "Trust Index"}], to_measures=[{"scale": "Perf Index"}])


@pytest.fixture
def world():
    tag = uuid.uuid4().hex[:8]
    client = _login(f"g6-{tag}@example.com")
    project_id = _project(client)
    source_id = _project_source(project_id)
    _prep_stage(project_id)
    gap_id = _selected_gap(project_id)
    return client, project_id, gap_id, source_id



# ---- EC-8.2 (research question linked to a selected gap) -----------------------------------


def test_ec_8_2_is_unmet_with_no_research_question(world):
    _, project_id, _, _ = world
    with SessionLocal() as db:
        project = db.get(Project, uuid.UUID(project_id))
        condition = exit_conditions.ec_8_2(db, project)
    assert condition.met is False
    assert "no research question" in condition.message


def test_ec_8_2_is_met_once_a_question_is_linked_to_a_selected_gap(world):
    client, project_id, gap_id, _ = world
    _linked_research_question(client, project_id, gap_id)
    with SessionLocal() as db:
        project = db.get(Project, uuid.UUID(project_id))
        assert exit_conditions.ec_8_2(db, project).met is True


# ---- EC-8.3 (conceptual-model path with construct-measure alignment) -----------------------


def test_ec_8_3_is_unmet_with_no_conceptual_model_path(world):
    _, project_id, _, _ = world
    with SessionLocal() as db:
        project = db.get(Project, uuid.UUID(project_id))
        condition = exit_conditions.ec_8_3(db, project)
    assert condition.met is False
    assert "no conceptual-model path" in condition.message


def test_ec_8_3_is_unmet_when_a_path_construct_has_no_measure(world):
    client, project_id, _, source_id = world
    _conceptual_path(client, project_id, source_id, from_measures=[{"scale": "Trust Index"}], to_measures=[])
    with SessionLocal() as db:
        project = db.get(Project, uuid.UUID(project_id))
        assert exit_conditions.ec_8_3(db, project).met is False


def test_ec_8_3_is_met_once_every_construct_in_a_path_has_a_measure(world):
    client, project_id, _, source_id = world
    _aligned_conceptual_model(client, project_id, source_id)
    with SessionLocal() as db:
        project = db.get(Project, uuid.UUID(project_id))
        assert exit_conditions.ec_8_3(db, project).met is True


# ---- G6 gate wiring (EC-8.2, EC-8.3) --------------------------------------------------------


def test_g6_approval_is_refused_while_neither_condition_is_met(world):
    client, project_id, _, _ = world
    response = client.post(f"/api/projects/{project_id}/gates/G6/approve")
    assert response.status_code == 409, response.text
    unmet_ids = {item["id"] for item in response.json()["detail"]["required"]}
    assert {"EC-8.2", "EC-8.3"} <= unmet_ids


def test_g6_approval_is_refused_while_only_the_research_question_is_present(world):
    client, project_id, gap_id, _ = world
    _linked_research_question(client, project_id, gap_id)
    response = client.post(f"/api/projects/{project_id}/gates/G6/approve")
    assert response.status_code == 409, response.text
    unmet_ids = {item["id"] for item in response.json()["detail"]["required"]}
    assert unmet_ids == {"EC-8.3"}


def test_g6_approval_is_refused_while_only_the_conceptual_model_is_present(world):
    client, project_id, _, source_id = world
    _aligned_conceptual_model(client, project_id, source_id)
    response = client.post(f"/api/projects/{project_id}/gates/G6/approve")
    assert response.status_code == 409, response.text
    unmet_ids = {item["id"] for item in response.json()["detail"]["required"]}
    assert unmet_ids == {"EC-8.2"}


def test_g6_approves_once_a_research_question_and_an_aligned_conceptual_model_exist(world):
    client, project_id, gap_id, source_id = world
    _linked_research_question(client, project_id, gap_id)
    _aligned_conceptual_model(client, project_id, source_id)
    response = client.post(f"/api/projects/{project_id}/gates/G6/approve")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "approved"
