"""Link-check of a manuscript section (M6.2): the route reports orphan claims and citation problems; it changes nothing."""

import uuid

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Source


def _login(email):
    client = TestClient(app)
    user_id = client.post("/api/auth/development/login", json={"email": email, "display_name": "L"}).json()["id"]
    return client, user_id


def _project(client):
    return client.post("/api/projects", json={"title": "Link check"}).json()["id"]


def _section(client, pid, title, position):
    response = client.post(f"/api/projects/{pid}/documents", json={"title": title, "position": position})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _source(pid, title, verified):
    with SessionLocal() as db:
        row = Source(project_id=uuid.UUID(pid), title=title, authors=["Lee, Ann"], year=2020, metadata_verified=verified)
        db.add(row)
        db.commit()
        return str(row.id)


def _cite(source_id):
    return {"type": "cite", "attrs": {"source_id": source_id}}


def _save(client, pid, did, content):
    response = client.post(
        f"/api/projects/{pid}/documents/{did}/versions",
        json={"base_number": 0, "client_save_id": uuid.uuid4().hex, "client_session_id": "s", "content_json": content},
    )
    assert response.status_code == 201, response.text


def _check(client, pid, did):
    response = client.get(f"/api/projects/{pid}/documents/{did}/link-check")
    assert response.status_code == 200, response.text
    return response.json()


def test_an_unsaved_section_with_an_orphan_claim_reports_it_and_no_version():
    client, _ = _login("link.orphan@example.org")
    pid = _project(client)
    intro = _section(client, pid, "Introduction", 0)
    claim = client.post(f"/api/projects/{pid}/claims", json={"text": "Remote teams share less", "section_id": intro}).json()
    report = _check(client, pid, intro)
    assert report["version_id"] is None
    assert report["ok"] is False
    assert report["orphan_claim_ids"] == [claim["id"]]
    assert [f["kind"] for f in report["findings"]] == ["orphan_claim"]


def test_a_cite_to_a_verified_source_appears_in_the_reference_list():
    client, _ = _login("link.verified@example.org")
    pid = _project(client)
    intro = _section(client, pid, "Introduction", 0)
    source = _source(pid, "Remote teams", verified=True)
    _save(client, pid, intro, {"type": "doc", "content": [{"type": "paragraph", "content": [_cite(source)]}]})
    report = _check(client, pid, intro)
    assert report["reference_source_ids"] == [source]
    assert report["cited_source_ids"] == [source]
    assert report["findings"] == []


def test_a_cited_source_that_is_later_unverified_is_flagged_and_drops_from_references():
    client, _ = _login("link.unverify@example.org")
    pid = _project(client)
    intro = _section(client, pid, "Introduction", 0)
    source = _source(pid, "Once verified", verified=True)
    _save(client, pid, intro, {"type": "doc", "content": [{"type": "paragraph", "content": [_cite(source)]}]})
    with SessionLocal() as db:
        db.get(Source, uuid.UUID(source)).metadata_verified = False
        db.commit()
    report = _check(client, pid, intro)
    assert report["reference_source_ids"] == []
    assert [f["kind"] for f in report["findings"]] == ["unverified_citation"]
    assert report["findings"][0]["subject"] == source


def test_claims_in_other_sections_are_not_reported():
    client, _ = _login("link.scope@example.org")
    pid = _project(client)
    intro = _section(client, pid, "Introduction", 0)
    method = _section(client, pid, "Method", 1)
    client.post(f"/api/projects/{pid}/claims", json={"text": "Sampling used a panel", "section_id": method})
    report = _check(client, pid, intro)
    assert report["orphan_claim_ids"] == []
    assert report["findings"] == []


def test_the_link_check_of_another_projects_section_is_not_found():
    client, _ = _login("link.foreign@example.org")
    mine = _project(client)
    other = _project(client)
    foreign = _section(client, other, "Their introduction", 0)
    assert client.get(f"/api/projects/{mine}/documents/{foreign}/link-check").status_code == 404
