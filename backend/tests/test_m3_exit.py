"""M3 exit check: an auditable API path from question to verified evidence and ranked gaps."""

import uuid

from fastapi.testclient import TestClient

from app import task_handlers, task_runner
from app.config import get_settings
from app.connectors.base import ConnectorBase, PaperRecord, SearchPage, SearchRequest
from app.database import SessionLocal
from app.main import app
from app.models import Source
from app.object_storage import fulltext_key
from gate_helpers import approve_earlier_gates
from tests.test_fulltext import make_pdf


class _SearchFixture(ConnectorBase):
    name = "openalex"

    def search(self, request: SearchRequest) -> SearchPage:
        return SearchPage(records=(
            PaperRecord(connector=self.name, external_id="W1", title="Study A", year=2022),
            PaperRecord(connector=self.name, external_id="W2", title="Study B", year=2023),
        ), total=2)


def test_m3_exit_question_search_screen_verified_evidence_and_ranked_gap(monkeypatch):
    monkeypatch.setattr(get_settings(), "connectors_enabled", ["openalex"])
    monkeypatch.setattr(task_handlers, "build_connector", lambda _name: _SearchFixture())
    client = TestClient(app)
    tag = uuid.uuid4().hex[:8]
    client.post("/api/auth/development/login", json={"email": f"m3-exit-{tag}@example.com", "display_name": "Scholar"})
    project_id = client.post("/api/projects", json={"title": "Remote work and productivity"}).json()["id"]
    question = "How does remote work affect productivity?"
    assert client.post(f"/api/projects/{project_id}/context", json={"kind": "question", "content": question}).status_code == 201
    assert client.put(f"/api/projects/{project_id}/criteria", json={"framework": "custom", "criteria": [
        {"kind": "include", "text": "Empirical remote-work studies"},
    ]}).status_code == 200

    approve_earlier_gates(project_id, "G2")
    queued = client.post(f"/api/projects/{project_id}/searches", json={
        "database": "openalex", "blocks": [{"label": "remote work", "terms": ["remote work"]},
                                              {"label": "outcome", "terms": ["productivity"]}],
        "filters": {"year_from": 2020}, "max_results": 10,
    })
    assert queued.status_code == 202 and queued.json()["status"] == "blocked"
    assert client.post(f"/api/projects/{project_id}/gates/G2/approve", json={"note": "Search strategy approved"}).status_code == 200
    assert task_runner.run_one_task() is True

    source_ids = []
    for external_id, title, country in (("W1", "Study A", "UK"), ("W2", "Study B", "Canada")):
        source = client.post(f"/api/projects/{project_id}/sources", json={
            "title": title, "year": 2022, "source_ids": {"openalex": external_id},
        })
        assert source.status_code == 201
        source_ids.append((source.json()["id"], country))

    pdfs = {}
    for source_id, country in source_ids:
        key = fulltext_key(uuid.UUID(project_id), uuid.UUID(source_id), "pdf")
        pdfs[key] = make_pdf([["Output scale", f"100 knowledge workers in {country}",
                               "Remote work improved productivity", "Survey regression"]])
        with SessionLocal() as db:
            db.get(Source, uuid.UUID(source_id)).fulltext_path = key
            db.commit()

    class Store:
        def get(self, key):
            return pdfs[key]

    from app.routers import extractions as extraction_routes
    monkeypatch.setattr(extraction_routes, "get_object_store", lambda _settings: Store())
    for source_id, country in source_ids:
        body = {"source_id": source_id, "fields": {
            "measures": [{"construct": "productivity", "scale": "Output scale", "reliability": None}],
            "sample": {"n": 100, "population": "knowledge workers", "country": country},
            "method": {"design": "Survey", "analysis": "Regression"},
            "key_findings": [{"statement": "Remote work improved productivity", "effect_size": None, "p_or_ci": None}],
        }, "evidence": {
            "measures": {"quote": "Output scale", "page": 1},
            "sample": {"quote": f"100 knowledge workers in {country}", "page": 1},
            "method": {"quote": "Survey regression", "page": 1},
            "key_findings": {"quote": "Remote work improved productivity", "page": 1},
        }}
        extraction = client.post(f"/api/projects/{project_id}/extractions", json=body)
        assert extraction.status_code == 201, extraction.text
        assert client.post(f"/api/projects/{project_id}/extractions/{extraction.json()['id']}/verify", json={
            "confirmed_critical_fields": ["measures", "sample", "key_findings"]
        }).status_code == 200
        assert client.post(f"/api/projects/{project_id}/screening/decisions", json={
            "source_id": source_id, "decision": "include"
        }).status_code == 201

    first, second = [source_id for source_id, _country in source_ids]
    for statement, priority in (("No comparative evidence across national contexts.", 8),
                                ("No longitudinal evidence is represented.", 3)):
        created = client.post(f"/api/projects/{project_id}/gaps", json={
            "type": "contextual", "statement": statement, "supporting_paper_ids": [first, second], "priority": priority,
        })
        assert created.status_code == 201, created.text
        assert client.patch(f"/api/projects/{project_id}/gaps/{created.json()['id']}", json={"selected": True}).status_code == 200

    assert client.get(f"/api/projects/{project_id}/context").json()[0]["content"] == question
    search = client.get(f"/api/projects/{project_id}/searches").json()["searches"][0]
    run = search["versions"][0]
    assert search["database"] == "openalex" and search["query_string"] == '("remote work") AND ("productivity")'
    assert search["filters"] == {"year_from": 2020} and search["exact"] is True
    assert run["n_results"] == 2 and run["counts"]["unique"] == 2 and run["counts"]["truncated"] is False
    prisma = client.get(f"/api/projects/{project_id}/prisma").json()
    assert prisma["identification"]["identified"] == 2
    assert prisma["screening"]["title_abstract"]["included"] == 2
    evidence = client.get(f"/api/projects/{project_id}/extractions").json()
    assert len(evidence) == 2 and all(row["verified_by_human"] for row in evidence)
    ranked = client.get(f"/api/projects/{project_id}/gaps").json()
    assert [(row["priority"], row["selected"]) for row in ranked] == [(8, True), (3, True)]
