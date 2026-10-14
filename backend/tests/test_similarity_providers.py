"""Institutional similarity integration point (X.31.15): the interface and a registry that says "not configured".

No connector exists until the licence question (Q17) is answered, so every institutional provider is unavailable.
"""

import pytest
from fastapi.testclient import TestClient

from app import similarity_providers as sp
from app.config import Settings, get_settings
from app.main import app


class FakeProvider:
    name = "fake"

    def submit(self, text, metadata):
        return "ref-1"

    def result(self, provider_ref):
        return [sp.ProviderFinding(start=0, end=5, score=0.9, matched="Some source")]


@pytest.fixture(autouse=True)
def clean_registry():
    saved = dict(sp.CONNECTORS)
    sp.CONNECTORS.clear()
    yield
    sp.CONNECTORS.clear()
    sp.CONNECTORS.update(saved)


def test_no_connector_ships_so_every_institutional_provider_is_unavailable():
    assert sp.CONNECTORS == {}
    assert sp.institutional_provider(Settings(similarity_institutional_provider="turnitin")) is None
    assert sp.institutional_provider(Settings()) is None


def test_a_named_but_unregistered_provider_is_still_not_configured():
    status = sp.provider_statuses(Settings(similarity_institutional_provider="turnitin"))

    assert status == [
        {"id": "project_sources", "label": "Project sources", "available": True, "hint": None},
        {"id": "institutional", "label": "Institutional check", "available": False, "hint": sp.NOT_CONNECTED_HINT},
    ]


def test_a_registered_connector_is_used_only_when_the_setting_names_it():
    sp.CONNECTORS["fake"] = lambda settings: FakeProvider()

    assert sp.institutional_provider(Settings()) is None
    provider = sp.institutional_provider(Settings(similarity_institutional_provider="fake"))
    assert provider is not None
    assert provider.result(provider.submit("text", {})) == [sp.ProviderFinding(start=0, end=5, score=0.9, matched="Some source")]
    assert sp.provider_statuses(Settings(similarity_institutional_provider="fake"))[1]["available"] is True


def test_the_providers_endpoint_lists_both_and_marks_institutional_unavailable():
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": "sim-providers@example.org", "display_name": "P"})
    project_id = client.post("/api/projects", json={"title": "Providers"}).json()["id"]

    response = client.get(f"/api/projects/{project_id}/similarity-providers")

    assert response.status_code == 200
    assert response.json()[1] == {"id": "institutional", "label": "Institutional check", "available": False, "hint": "Not connected: ask your institution"}
    assert get_settings().similarity_institutional_provider == ""
