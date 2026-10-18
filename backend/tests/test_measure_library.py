import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.measure_library import BY_KEY, MEASURES, build_draft


def test_the_library_is_the_six_approved_measures_with_licences_to_check():
    assert sorted(BY_KEY) == sorted(["bfi", "mbi", "phq9", "pss", "ucla_loneliness", "rses"])
    assert all(m.licence == "to check" for m in MEASURES)
    assert all(m.citation.strip() for m in MEASURES)


def test_identifiers_are_only_the_verified_ones():
    assert BY_KEY["phq9"].doi == "10.1046/j.1525-1497.2001.016009606.x"
    assert BY_KEY["pss"].pmid == "6668417"
    assert BY_KEY["ucla_loneliness"].doi == "10.1207/s15327752jpa6601_2"
    assert BY_KEY["bfi"].doi is None and BY_KEY["rses"].doi is None  # not verified, so not stored


def test_draft_lists_each_chosen_measure_once_and_sorts_references():
    draft = build_draft(["pss", "bfi", "pss"], ["I feel calm."])
    assert [m["key"] for m in draft["measures"]] == ["pss", "bfi"]
    assert draft["reference_list"] == sorted(BY_KEY[k].citation for k in ("pss", "bfi"))
    assert draft["own_items"] == ["I feel calm."]


def test_draft_refuses_an_unknown_key_rather_than_guessing():
    with pytest.raises(KeyError):
        build_draft(["not-a-measure"], [])


def test_draft_contains_no_item_wording():
    draft = build_draft(["phq9"], [])
    assert "items" not in draft["measures"][0]
    assert "licence" in draft["measures"][0]
    assert "not included" in draft["note"]


@pytest.fixture
def client():
    c = TestClient(app)
    c.post("/api/auth/development/login", json={"email": f"ml-{uuid.uuid4().hex}@example.com", "display_name": "M"})
    return c


def test_library_route_lists_the_six(client):
    pid = client.post("/api/projects", json={"title": "Measures"}).json()["id"]
    body = client.get(f"/api/projects/{pid}/instrument/measures").json()
    assert {m["key"] for m in body["measures"]} == set(BY_KEY)


def test_draft_route_refuses_an_unknown_key(client):
    pid = client.post("/api/projects", json={"title": "Measures"}).json()["id"]
    response = client.post(f"/api/projects/{pid}/instrument/draft", json={"measure_keys": ["made-up"]})
    assert response.status_code == 422
    assert "made-up" in response.text


def test_draft_route_returns_references_and_own_items(client):
    pid = client.post("/api/projects", json={"title": "Measures"}).json()["id"]
    body = client.post(
        f"/api/projects/{pid}/instrument/draft", json={"measure_keys": ["rses"], "own_items": ["  My own item  ", " "]}
    ).json()
    assert body["measures"][0]["key"] == "rses"
    assert body["own_items"] == ["My own item"]  # blank entries dropped, whitespace stripped
