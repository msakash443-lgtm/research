import uuid

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.database import SessionLocal
from app.main import app
from app.models import Gate, GateCode, GateStatus, Source

BIB = "@article{a, title={My own paper}, author={Lee, Ann}, year={2020}, doi={10.1234/own}}"


def _project():
    client = TestClient(app)
    tag = uuid.uuid4().hex[:8]
    client.post("/api/auth/development/login", json={"email": f"own-{tag}@example.com", "display_name": "O"})
    pid = client.post("/api/projects", json={"title": "Own", "research_question": "Does it work?"}).json()["id"]
    return client, pid


def _decisions(client, pid):
    return client.get(f"/api/projects/{pid}/screening/decisions").json()


def test_own_paper_from_fields_is_unverified_manual_and_included_at_both_stages():
    client, pid = _project()
    response = client.post(f"/api/projects/{pid}/sources/own", json={"title": "My paper", "doi": "10.1234/x", "year": 2021})
    assert response.status_code == 201
    source = response.json()
    assert source["origin"] == "manual" and source["metadata_verified"] is False
    rows = [d for d in _decisions(client, pid) if d["source_id"] == source["id"]]
    assert {(d["stage"], d["decision"], d["decided_by"]) for d in rows} == {("title_abstract", "include", "human"), ("full_text", "include", "human")}
    assert all(d["note"] == "added by researcher" for d in rows)


def test_own_paper_from_one_bibtex_entry():
    client, pid = _project()
    source = client.post(f"/api/projects/{pid}/sources/own", json={"bibtex": BIB}).json()
    assert source["title"] == "My own paper" and source["doi"] == "10.1234/own" and source["year"] == 2020
    assert len(_decisions(client, pid)) == 2


def test_bibtex_with_zero_or_several_entries_is_refused_and_nothing_is_stored():
    client, pid = _project()
    for text in ("not bibtex", BIB + "\n" + BIB.replace("own", "two")):
        assert client.post(f"/api/projects/{pid}/sources/own", json={"bibtex": text}).status_code == 422
    assert client.get(f"/api/projects/{pid}/sources").json() == []


def test_giving_both_or_neither_input_is_refused():
    client, pid = _project()
    base = f"/api/projects/{pid}/sources/own"
    assert client.post(base, json={}).status_code == 422
    assert client.post(base, json={"bibtex": BIB, "title": "Also"}).status_code == 422


def test_a_doi_already_in_the_project_is_a_409_and_adds_no_second_source_or_decision():
    client, pid = _project()
    base = f"/api/projects/{pid}/sources/own"
    assert client.post(base, json={"bibtex": BIB}).status_code == 201
    assert client.post(base, json={"title": "Again", "doi": "10.1234/own"}).status_code == 409
    assert len(client.get(f"/api/projects/{pid}/sources").json()) == 1
    assert len(_decisions(client, pid)) == 2


def test_nothing_is_added_once_gate_g3_is_approved():
    client, pid = _project()
    with SessionLocal() as db:
        gate = db.scalar(select(Gate).where(Gate.project_id == uuid.UUID(pid), Gate.code == GateCode.G3))
        if gate is None:
            gate = Gate(project_id=uuid.UUID(pid), code=GateCode.G3)
            db.add(gate)
        gate.status = GateStatus.approved
        db.commit()
    assert client.post(f"/api/projects/{pid}/sources/own", json={"title": "Late"}).status_code == 409
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Source).where(Source.project_id == uuid.UUID(pid))) == 0


def test_adding_an_own_paper_is_audited():
    client, pid = _project()
    client.post(f"/api/projects/{pid}/sources/own", json={"title": "Audited"})
    actions = [e["action"] for e in client.get(f"/api/projects/{pid}/audit").json()]
    assert "source.own_paper_added" in actions
