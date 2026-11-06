import uuid

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Source


ALL_OK = [
    {"item": "data_availability", "status": "ok"},
    {"item": "method_fit", "status": "ok"},
    {"item": "ethics_risk", "status": "ok"},
    {"item": "time_and_skills", "status": "ok"},
]


def _login(client: TestClient) -> None:
    client.post("/api/auth/development/login", json={"email": f"idea-{uuid.uuid4().hex}@example.com", "display_name": "I"})


def _project(client: TestClient) -> str:
    return client.post("/api/projects", json={"title": "Idea brief"}).json()["id"]


def _idea(client: TestClient, pid: str, content: str) -> str:
    response = client.post(f"/api/projects/{pid}/context", json={"kind": "idea", "content": content})
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


def _source(client: TestClient, pid: str, title: str, abstract: str) -> None:
    response = client.post(f"/api/projects/{pid}/sources", json={"title": title, "abstract": abstract, "year": 2020})
    assert response.status_code in (200, 201), response.text


def _brief(**overrides) -> dict:
    body = {"problem": "Teams stall", "why_it_matters": "Delivery slips", "feasibility": ALL_OK}
    body.update(overrides)
    return body


def test_before_any_save_the_brief_does_not_exist_and_is_not_ready():
    client = TestClient(app)
    _login(client)
    pid = _project(client)
    payload = client.get(f"/api/projects/{pid}/idea-brief").json()
    assert payload["exists"] is False
    assert payload["ready_for_g1"] is False
    assert payload["ideas"] == []


def test_each_idea_lists_the_sources_it_was_scored_against():
    client = TestClient(app)
    _login(client)
    pid = _project(client)
    _source(client, pid, "Trust in agile teams", "Trust between team members predicts sharing of knowledge.")
    _idea(client, pid, "Trust predicts knowledge sharing in remote teams")
    _idea(client, pid, "Quantum gravity lattice models")
    by_text = {idea["content"]: idea for idea in client.get(f"/api/projects/{pid}/idea-brief").json()["ideas"]}
    trust = by_text["Trust predicts knowledge sharing in remote teams"]
    quantum = by_text["Quantum gravity lattice models"]
    assert trust["novelty"]["comparators"][0]["title"] == "Trust in agile teams"
    assert "trust" in trust["novelty"]["comparators"][0]["shared_terms"]
    assert quantum["novelty"]["distinctiveness"] == 1.0
    assert quantum["novelty"]["comparators"] == []


def test_the_brief_is_ready_only_when_an_idea_is_chosen_and_every_item_is_answered_without_a_blocker():
    client = TestClient(app)
    _login(client)
    pid = _project(client)
    idea_id = _idea(client, pid, "Trust predicts knowledge sharing")
    url = f"/api/projects/{pid}/idea-brief"
    assert client.put(url, json=_brief()).json()["ready_for_g1"] is False
    unanswered = client.put(url, json=_brief(selected_idea_id=idea_id, feasibility=ALL_OK[:3])).json()
    assert unanswered["ready_for_g1"] is False
    assert unanswered["open_items"] == ["time_and_skills"]
    ready = client.put(url, json=_brief(selected_idea_id=idea_id)).json()
    assert ready["ready_for_g1"] is True
    assert ready["open_items"] == [] and ready["blockers"] == []


def test_a_blocker_is_named_and_keeps_the_brief_from_being_ready():
    client = TestClient(app)
    _login(client)
    pid = _project(client)
    idea_id = _idea(client, pid, "Trust predicts knowledge sharing")
    feasibility = [{"item": "data_availability", "status": "blocker", "note": "No access to the team logs"}] + ALL_OK[1:]
    payload = client.put(f"/api/projects/{pid}/idea-brief", json=_brief(selected_idea_id=idea_id, feasibility=feasibility)).json()
    assert payload["blockers"] == ["data_availability"]
    assert payload["ready_for_g1"] is False


def test_unsure_counts_as_an_open_item_not_as_an_answer():
    client = TestClient(app)
    _login(client)
    pid = _project(client)
    idea_id = _idea(client, pid, "Trust predicts knowledge sharing")
    feasibility = ALL_OK[:3] + [{"item": "time_and_skills", "status": "unsure"}]
    payload = client.put(f"/api/projects/{pid}/idea-brief", json=_brief(selected_idea_id=idea_id, feasibility=feasibility)).json()
    assert payload["open_items"] == ["time_and_skills"]
    assert payload["ready_for_g1"] is False


def test_an_idea_from_another_project_cannot_be_selected():
    client = TestClient(app)
    _login(client)
    mine = _project(client)
    theirs = _project(client)
    other_idea = _idea(client, theirs, "Someone else's idea")
    response = client.put(f"/api/projects/{mine}/idea-brief", json=_brief(selected_idea_id=other_idea))
    assert response.status_code == 404


def test_a_context_item_that_is_not_an_idea_cannot_be_selected():
    client = TestClient(app)
    _login(client)
    pid = _project(client)
    question = client.post(f"/api/projects/{pid}/context", json={"kind": "question", "content": "Why?"}).json()["id"]
    response = client.put(f"/api/projects/{pid}/idea-brief", json=_brief(selected_idea_id=question))
    assert response.status_code == 422


def test_a_merged_away_source_is_not_used_as_a_comparator():
    client = TestClient(app)
    _login(client)
    pid = _project(client)
    _source(client, pid, "Trust in agile teams", "Trust between team members predicts sharing of knowledge.")
    _idea(client, pid, "Trust predicts knowledge sharing")
    with SessionLocal() as db:
        db.query(Source).filter(Source.project_id == uuid.UUID(pid)).update({"merged_into": uuid.uuid4()})
        db.commit()
    trust = client.get(f"/api/projects/{pid}/idea-brief").json()["ideas"][0]
    assert trust["novelty"]["comparators"] == []


def test_saving_a_ready_brief_does_not_approve_g1():
    client = TestClient(app)
    _login(client)
    pid = _project(client)
    idea_id = _idea(client, pid, "Trust predicts knowledge sharing")
    client.put(f"/api/projects/{pid}/idea-brief", json=_brief(selected_idea_id=idea_id))
    gates = client.get(f"/api/projects/{pid}/gates").json()
    g1 = next(g for g in gates if g["code"] == "G1")
    assert g1["status"] != "approved"


def test_the_brief_lists_duplicate_ideas_clusters_and_candidate_constructs_across_the_project():
    client = TestClient(app)
    _login(client)
    pid = _project(client)
    first = _idea(client, pid, "Trust predicts knowledge sharing")
    repeat = _idea(client, pid, "trust predicts knowledge sharing!")
    related = _idea(client, pid, "Trust predicts knowledge sharing in remote teams")
    unrelated = _idea(client, pid, "Quantum lattice gravity")
    payload = client.get(f"/api/projects/{pid}/idea-brief").json()
    ((dropped, kept),) = payload["duplicate_ideas"].items()
    assert {dropped, kept} == {first, repeat}
    clusters = {frozenset(c["member_ids"]) for c in payload["clusters"]}
    assert clusters == {frozenset({kept, related}), frozenset({unrelated})}
    constructs = {(c["term"], frozenset(c["idea_ids"])) for c in payload["constructs"]}
    assert ("trust", frozenset({kept, related})) in constructs
