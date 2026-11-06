import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app import survey_google_forms as gf
from app.main import app
from app.routers import datasets as router_module


@pytest.fixture()
def owner():
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": f"sv-{uuid.uuid4().hex}@example.com", "display_name": "S"})
    pid = client.post("/api/projects", json={"title": "Survey"}).json()["id"]
    return client, pid


def _settings(token):
    return lambda: SimpleNamespace(google_forms_access_token=SecretStr(token) if token else None)


def test_google_forms_import_is_503_without_a_token(owner, monkeypatch):
    monkeypatch.setattr(router_module, "get_settings", _settings(None))
    client, pid = owner
    r = client.post(f"/api/projects/{pid}/datasets/google-forms", json={"form_id": "F1", "name": "wave1"})
    assert r.status_code == 503
    assert "GOOGLE_FORMS_ACCESS_TOKEN" in r.json()["detail"]
    assert client.get(f"/api/projects/{pid}/datasets").json() == []


def test_google_forms_import_stores_a_raw_hashed_version(owner, monkeypatch):
    monkeypatch.setattr(router_module, "get_settings", _settings("tok"))
    monkeypatch.setattr(gf, "fetch_table", lambda form_id, token, **kw: (
        ["response_id", "created_at", "submitted_at", "Age"],
        [["r1", "2026-10-01T10:00:00Z", "2026-10-01T10:05:00Z", "34"]],
    ))
    client, pid = owner
    r = client.post(f"/api/projects/{pid}/datasets/google-forms", json={"form_id": "F1", "name": "wave1"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["kind"] == "raw" and body["row_count"] == 1 and body["intact"] is True
    assert body["column_names"][-1] == "Age"


def test_bad_form_id_is_422_and_upstream_failure_is_502(owner, monkeypatch):
    monkeypatch.setattr(router_module, "get_settings", _settings("tok"))
    client, pid = owner
    bad = client.post(f"/api/projects/{pid}/datasets/google-forms", json={"form_id": "../x", "name": "w"})
    assert bad.status_code == 422

    def boom(form_id, token, **kw):
        raise gf.SurveyError("Google Forms refused the access token.")
    monkeypatch.setattr(gf, "fetch_table", boom)
    up = client.post(f"/api/projects/{pid}/datasets/google-forms", json={"form_id": "F1", "name": "w"})
    assert up.status_code == 502 and "token" in up.json()["detail"]


def test_quality_route_flags_rows_and_creates_no_version(owner):
    client, pid = owner
    text = "response_id,created_at,submitted_at,q1,check\nr1,t,t,3,pick 3\nr2,t,t,3,pick 1\n"
    raw = client.post(f"/api/projects/{pid}/datasets", json={"name": "s", "csv_text": text}).json()
    r = client.post(
        f"/api/projects/{pid}/datasets/{raw['id']}/quality",
        json={"attention": {"check": "pick 3"}, "straightline_min_items": 2},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["flagged_count"] == 1 and body["flags"][0]["response_id"] == "r2"
    assert body["removes_rows"] is False
    assert len(client.get(f"/api/projects/{pid}/datasets").json()) == 1


def test_quality_route_rejects_unknown_attention_column(owner):
    client, pid = owner
    raw = client.post(f"/api/projects/{pid}/datasets", json={"name": "s", "csv_text": "a\n1\n"}).json()
    r = client.post(f"/api/projects/{pid}/datasets/{raw['id']}/quality", json={"attention": {"zzz": "x"}})
    assert r.status_code == 422


def test_quality_route_refuses_mixed_timestamps_as_422_not_500(owner):
    client, pid = owner
    text = "response_id,created_at,submitted_at,q1\nr1,2026-10-01T10:00:00,2026-10-01T10:05:00+00:00,3\n"
    raw = client.post(f"/api/projects/{pid}/datasets", json={"name": "s", "csv_text": text}).json()
    r = client.post(
        f"/api/projects/{pid}/datasets/{raw['id']}/quality",
        json={"start_column": "created_at"},
    )
    assert r.status_code == 422, r.text
    assert "mix a time zone" in r.json()["detail"]


def test_quality_route_runs_grid_and_speeder_checks_only_when_named(owner):
    client, pid = owner
    text = (
        "response_id,created_at,submitted_at,Age,q1,q2,q3\n"
        "r1,2026-10-01T10:00:00+00:00,2026-10-01T10:00:00+00:00,34,3,3,3\n"
    )
    raw = client.post(f"/api/projects/{pid}/datasets", json={"name": "s", "csv_text": text}).json()
    url = f"/api/projects/{pid}/datasets/{raw['id']}/quality"
    not_named = client.post(url, json={"straightline_min_items": 3}).json()
    assert not_named["flags"] == [] and not_named["checks"] == {"straight_lining": False, "speeder": False}
    named = client.post(url, json={"straightline_min_items": 3, "grid_columns": ["q1", "q2", "q3"],
                                   "start_column": "created_at"}).json()
    reasons = named["flags"][0]["reasons"]
    assert "straight_lined" in reasons and "speeder" in reasons
    assert named["checks"] == {"straight_lining": True, "speeder": True}
