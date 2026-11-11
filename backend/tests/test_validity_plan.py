import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.measure_library import BY_KEY
from app.validity_plan import build_plan


def test_each_chosen_measure_appears_once_with_its_citation():
    plan = build_plan(["pss", "bfi", "pss"], 0)
    assert [i["key"] for i in plan["instruments"]] == ["pss", "bfi"]
    assert plan["instruments"][0]["citation"] == BY_KEY["pss"].citation


def test_own_items_add_steps_and_none_means_no_steps():
    assert build_plan(["pss"], 0)["own_items"]["steps"] == []
    with_items = build_plan([], 5)
    assert with_items["own_items"]["count"] == 5 and with_items["own_items"]["steps"]


def test_every_decision_is_left_bracketed_and_no_figure_is_supplied():
    plan = build_plan(["phq9"], 2)
    assert all(d.startswith("[") and "to decide" in d for d in plan["decisions"])
    assert all(r["planned"].startswith("[") for r in plan["reliability"] + plan["validity"])
    text = str(plan)
    assert "0.7" not in text and ".70" not in text and "alpha =" not in text


def test_unknown_key_and_empty_request_are_refused():
    with pytest.raises(KeyError):
        build_plan(["made-up"], 0)
    with pytest.raises(ValueError):
        build_plan([], 0)


@pytest.fixture
def client():
    c = TestClient(app)
    c.post("/api/auth/development/login", json={"email": f"vp-{uuid.uuid4().hex}@example.com", "display_name": "V"})
    return c


def test_route_returns_a_plan_and_refuses_bad_input_with_422(client):
    pid = client.post("/api/projects", json={"title": "Plan"}).json()["id"]
    url = f"/api/projects/{pid}/instrument/validity-plan"
    ok = client.post(url, json={"measure_keys": ["rses"], "own_item_count": 4})
    assert ok.status_code == 200 and ok.json()["instruments"][0]["key"] == "rses"
    assert client.post(url, json={"measure_keys": ["made-up"]}).status_code == 422
    assert client.post(url, json={}).status_code == 422
    assert client.post(url, json={"own_item_count": -1}).status_code == 422
