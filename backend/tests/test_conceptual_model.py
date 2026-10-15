import uuid

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Source


@pytest.fixture
def world():
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": f"model-{uuid.uuid4().hex}@example.com", "display_name": "M"})
    pid = client.post("/api/projects", json={"title": "Conceptual model"}).json()["id"]

    def construct(name, measures=()):
        response = client.post(f"/api/projects/{pid}/constructs", json={
            "name": name, "definition": "Definition", "author": "Smith", "measures": list(measures)})
        assert response.status_code == 201, response.text
        return response.json()["id"]

    ids = {"trust": construct("Trust", [{"scale": "Trust Index"}]), "sharing": construct("Knowledge sharing", [{"scale": "KS"}]),
           "performance": construct("Team performance"), "distance": construct("Distance")}
    source = client.post(f"/api/projects/{pid}/sources", json={"title": "Smith 2020"}).json()["id"]
    return client, pid, ids, source


def _path(client, pid, **body):
    return client.post(f"/api/projects/{pid}/conceptual-model/paths", json={"rationale": "Prior studies report it.", **body})


def test_paths_render_as_mermaid_with_mediation_and_moderation(world):
    client, pid, ids, source = world
    assert _path(client, pid, from_construct_id=ids["trust"], to_construct_id=ids["performance"], kind="mediation",
                 via_construct_id=ids["sharing"], source_ids=[source]).status_code == 201
    assert _path(client, pid, from_construct_id=ids["trust"], to_construct_id=ids["sharing"], kind="moderation",
                 via_construct_id=ids["distance"], source_ids=[source]).status_code == 201
    model = client.get(f"/api/projects/{pid}/conceptual-model").json()
    assert len(model["paths"]) == 2
    diagram = model["mermaid"]
    assert diagram.startswith("flowchart LR")
    assert '"Trust #40;Smith#41;"' in diagram
    nodes = {line.split("[")[1].split(" #40;")[0].strip('"'): line.split("[")[0].strip() for line in diagram.splitlines()[1:] if "[" in line}
    t, k, p, d = nodes["Trust"], nodes["Knowledge sharing"], nodes["Team performance"], nodes["Distance"]
    assert f"{t} --> {k}" in diagram and f"{k} --> {p}" in diagram
    assert f"{d} -.->|moderates| {k}" in diagram
    assert diagram.count(f"{t} --> {k}") == 1  # the shared edge is drawn once


def test_alignment_flags_model_constructs_without_measures(world):
    client, pid, ids, source = world
    _path(client, pid, from_construct_id=ids["trust"], to_construct_id=ids["performance"], kind="direct", source_ids=[source])
    model = client.get(f"/api/projects/{pid}/conceptual-model").json()
    rows = {row["name"]: row for row in model["alignment"]}
    assert rows["Trust"]["aligned"] and rows["Trust"]["in_model"]
    assert not rows["Team performance"]["aligned"] and rows["Team performance"]["in_model"]
    assert not rows["Distance"]["in_model"]
    assert model["unaligned_in_model"] == 1
    assert "M4.4" in model["alignment_note"]


@pytest.mark.parametrize("body", [
    {"kind": "direct", "source_ids": []},  # every path needs literature
    {"kind": "mediation"},  # mediator missing
    {"kind": "direct", "via": "sharing"},  # a direct path has no mediator
    {"kind": "direct", "to": "trust"},  # self-loop
    {"kind": "mediation", "via": "trust"},  # mediator must be a third construct
])
def test_malformed_paths_are_rejected(world, body):
    client, pid, ids, source = world
    payload = {"from_construct_id": ids["trust"], "to_construct_id": ids[body.get("to", "performance")], "kind": body["kind"],
               "source_ids": body.get("source_ids", [source])}
    if "via" in body:
        payload["via_construct_id"] = ids[body["via"]]
    assert _path(client, pid, **payload).status_code == 422


def test_constructs_and_sources_must_belong_to_the_project(world):
    client, pid, ids, source = world
    other = client.post("/api/projects", json={"title": "Other"}).json()["id"]
    foreign_source = client.post(f"/api/projects/{other}/sources", json={"title": "Elsewhere"}).json()["id"]
    foreign_construct = client.post(f"/api/projects/{other}/constructs", json={"name": "X", "definition": "D", "author": "A"}).json()["id"]
    assert _path(client, pid, from_construct_id=ids["trust"], to_construct_id=ids["performance"], kind="direct",
                 source_ids=[foreign_source]).status_code == 422
    assert _path(client, pid, from_construct_id=ids["trust"], to_construct_id=foreign_construct, kind="direct",
                 source_ids=[source]).status_code == 422


def test_a_merged_source_cannot_back_a_path(world):
    client, pid, ids, source = world
    duplicate = client.post(f"/api/projects/{pid}/sources", json={"title": "Smith 2020 (duplicate)"}).json()["id"]
    with SessionLocal() as db:
        db.get(Source, uuid.UUID(duplicate)).merged_into = uuid.UUID(source)
        db.commit()
    response = _path(client, pid, from_construct_id=ids["trust"], to_construct_id=ids["performance"], kind="direct",
                     source_ids=[duplicate])
    assert response.status_code == 422
    assert "merged" in response.json()["detail"]


def test_duplicate_direct_path_conflicts_and_delete_removes_it(world):
    client, pid, ids, source = world
    body = {"from_construct_id": ids["trust"], "to_construct_id": ids["performance"], "kind": "direct", "source_ids": [source]}
    created = _path(client, pid, **body)
    assert created.status_code == 201
    assert _path(client, pid, **body).status_code == 409
    path_id = created.json()["id"]
    assert client.delete(f"/api/projects/{pid}/conceptual-model/paths/{path_id}").status_code == 204
    assert client.get(f"/api/projects/{pid}/conceptual-model").json()["paths"] == []
    assert client.delete(f"/api/projects/{pid}/conceptual-model/paths/{path_id}").status_code == 404


def test_graphviz_matches_the_paths_and_escapes_labels(world):
    client, pid, ids, source = world
    quoted = client.post(f"/api/projects/{pid}/constructs", json={"name": 'Say "hi"', "definition": "D", "author": "Lee"}).json()["id"]
    _path(client, pid, from_construct_id=quoted, to_construct_id=ids["sharing"], kind="moderation",
          via_construct_id=ids["distance"], source_ids=[source])
    model = client.get(f"/api/projects/{pid}/conceptual-model").json()
    dot = model["graphviz"]
    assert dot.startswith("digraph conceptual_model {") and dot.endswith("}")
    assert 'label="Say \\"hi\\" (Lee)"' in dot
    assert dot.count("->") == 2 and 'style=dashed, label="moderates"' in dot
    assert '"Say #quot;hi#quot; #40;Lee#41;"' in model["mermaid"]
