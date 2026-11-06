"""X.142: opt-in keyset pagination on the sources and notes lists."""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.pagination import InvalidCursor, decode_cursor, encode_cursor


def _login(email):
    client = TestClient(app)
    assert client.post("/api/auth/development/login", json={"email": email, "display_name": "P"}).status_code == 200
    return client


def _project_with_sources(client, count):
    project_id = client.post("/api/projects", json={"title": "Paging"}).json()["id"]
    for i in range(count):
        created = client.post(f"/api/projects/{project_id}/sources", json={
            "title": f"Source {i}", "source_type": "journal article",
            "evidence_excerpt": f"Excerpt number {i}.", "locator": "p. 1",
        })
        assert created.status_code == 201
    return project_id


def _walk(client, url, limit):
    """Every id, page by page, and the number of pages."""
    ids, cursor, pages = [], None, 0
    while True:
        params = {"limit": limit} | ({"cursor": cursor} if cursor else {})
        response = client.get(url, params=params)
        assert response.status_code == 200
        page = response.json()
        assert len(page) <= limit
        ids += [row["id"] for row in page]
        pages += 1
        cursor = response.headers.get("X-Next-Cursor")
        if cursor is None:
            return ids, pages
        assert pages < 50, "pagination never ended"


def test_a_cursor_round_trips_and_a_tampered_cursor_is_refused():
    row_id = uuid.uuid4()
    assert decode_cursor(encode_cursor(row_id)) == row_id
    for bad in ("not base64!", "AAAA", ""):
        with pytest.raises(InvalidCursor):
            decode_cursor(bad)


def test_walking_every_page_of_sources_returns_each_source_exactly_once_in_list_order():
    # Sources created in one second share created_at on SQLite, so the id tiebreak is exercised.
    client = _login("pages1@example.com")
    project_id = _project_with_sources(client, 7)
    url = f"/api/projects/{project_id}/sources"
    full = [row["id"] for row in client.get(url).json()]
    assert len(full) == 7

    paged, pages = _walk(client, url, 3)

    assert paged == full
    assert pages == 3


def test_without_limit_the_sources_list_is_whole_and_has_no_cursor_header():
    client = _login("pages2@example.com")
    project_id = _project_with_sources(client, 4)
    response = client.get(f"/api/projects/{project_id}/sources")
    assert len(response.json()) == 4
    assert "X-Next-Cursor" not in response.headers


def test_an_exact_last_page_has_no_cursor():
    client = _login("pages3@example.com")
    project_id = _project_with_sources(client, 3)
    response = client.get(f"/api/projects/{project_id}/sources", params={"limit": 3})
    assert len(response.json()) == 3
    assert "X-Next-Cursor" not in response.headers


def test_bad_cursors_are_refused_with_400():
    client = _login("pages4@example.com")
    project_id = _project_with_sources(client, 2)
    other_project = _project_with_sources(client, 2)
    url = f"/api/projects/{project_id}/sources"
    foreign_cursor = client.get(f"/api/projects/{other_project}/sources", params={"limit": 1}).headers["X-Next-Cursor"]

    assert client.get(url, params={"limit": 1, "cursor": "garbage!"}).status_code == 400
    assert client.get(url, params={"limit": 1, "cursor": encode_cursor(uuid.uuid4())}).status_code == 400
    # A cursor naming a row from another project's list can't be used to page this one.
    assert client.get(url, params={"limit": 1, "cursor": foreign_cursor}).status_code == 400
    assert client.get(url, params={"cursor": foreign_cursor}).status_code == 400  # cursor without limit


def test_limit_is_bounded():
    client = _login("pages5@example.com")
    project_id = _project_with_sources(client, 1)
    url = f"/api/projects/{project_id}/sources"
    assert client.get(url, params={"limit": 0}).status_code == 422
    assert client.get(url, params={"limit": 501}).status_code == 422


def test_walking_every_page_of_notes_returns_each_note_exactly_once_even_with_equal_capture_times():
    client = _login("pages6@example.com")
    same_time = "2026-10-01T09:00:00+00:00"
    for i in range(5):
        captured = same_time if i < 3 else f"2026-10-0{i}T09:00:00+00:00"
        assert client.post("/api/notes", json={
            "client_id": f"n{i}", "kind": "typed", "body": f"Note {i}", "captured_at": captured,
        }).status_code == 201
    full = [row["id"] for row in client.get("/api/notes").json()]
    assert len(full) == 5

    paged, pages = _walk(client, "/api/notes", 2)

    assert paged == full
    assert pages == 3
