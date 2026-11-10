"""Link-check of a manuscript section (M6.2): the route reports orphan claims and citation problems; it changes nothing."""

import uuid

from app.database import SessionLocal
from app.models import Source
from tests.api_helpers import create_project, insert_source, sign_in_user


def _section(client, pid, title, position):
    response = client.post(f"/api/projects/{pid}/documents", json={"title": title, "position": position})
    assert response.status_code == 201, response.text
    return response.json()["id"]


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
    client, _ = sign_in_user("link.orphan@example.org")
    pid = create_project(client, "Link check")
    intro = _section(client, pid, "Introduction", 0)
    claim = client.post(f"/api/projects/{pid}/claims", json={"text": "Remote teams share less", "section_id": intro}).json()
    report = _check(client, pid, intro)
    assert report["version_id"] is None
    assert report["ok"] is False
    assert report["orphan_claim_ids"] == [claim["id"]]
    assert [f["kind"] for f in report["findings"]] == ["orphan_claim"]


def test_a_cite_to_a_verified_source_appears_in_the_reference_list():
    client, _ = sign_in_user("link.verified@example.org")
    pid = create_project(client, "Link check")
    intro = _section(client, pid, "Introduction", 0)
    source = insert_source(pid, title="Remote teams", authors=["Lee, Ann"], year=2020, metadata_verified=True)
    _save(client, pid, intro, {"type": "doc", "content": [{"type": "paragraph", "content": [_cite(source)]}]})
    report = _check(client, pid, intro)
    assert report["reference_source_ids"] == [source]
    assert report["cited_source_ids"] == [source]
    assert report["findings"] == []


def test_a_cited_source_that_is_later_unverified_is_flagged_and_drops_from_references():
    client, _ = sign_in_user("link.unverify@example.org")
    pid = create_project(client, "Link check")
    intro = _section(client, pid, "Introduction", 0)
    source = insert_source(pid, title="Once verified", authors=["Lee, Ann"], year=2020, metadata_verified=True)
    _save(client, pid, intro, {"type": "doc", "content": [{"type": "paragraph", "content": [_cite(source)]}]})
    with SessionLocal() as db:
        db.get(Source, uuid.UUID(source)).metadata_verified = False
        db.commit()
    report = _check(client, pid, intro)
    assert report["reference_source_ids"] == []
    assert [f["kind"] for f in report["findings"]] == ["unverified_citation"]
    assert report["findings"][0]["subject"] == source


def test_claims_in_other_sections_are_not_reported():
    client, _ = sign_in_user("link.scope@example.org")
    pid = create_project(client, "Link check")
    intro = _section(client, pid, "Introduction", 0)
    method = _section(client, pid, "Method", 1)
    client.post(f"/api/projects/{pid}/claims", json={"text": "Sampling used a panel", "section_id": method})
    report = _check(client, pid, intro)
    assert report["orphan_claim_ids"] == []
    assert report["findings"] == []


def test_the_link_check_of_another_projects_section_is_not_found():
    client, _ = sign_in_user("link.foreign@example.org")
    mine = create_project(client, "Link check")
    other = create_project(client, "Link check")
    foreign = _section(client, other, "Their introduction", 0)
    assert client.get(f"/api/projects/{mine}/documents/{foreign}/link-check").status_code == 404
