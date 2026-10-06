import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.connectors.access import CATALOGUE, AccessKind, is_enabled, unknown_connectors
from app.main import app


def _client(signed_in=True):
    client = TestClient(app)
    if signed_in:
        assert client.post("/api/auth/development/login", json={"email": "conn@example.com", "display_name": "C"}).status_code == 200
    return client


@pytest.fixture
def settings(monkeypatch):
    live = get_settings()
    monkeypatch.setattr(live, "connectors_enabled", [])
    monkeypatch.setattr(live, "connectors_allow_scraping", False)
    return live


def test_every_entry_has_a_valid_access_kind_and_unique_name():
    assert len(CATALOGUE) == len({i.name for i in CATALOGUE.values()})
    assert all(isinstance(i.access, AccessKind) for i in CATALOGUE.values())
    assert any(i.access is AccessKind.scraping for i in CATALOGUE.values())
    assert any(i.access is AccessKind.licensed for i in CATALOGUE.values())


def test_nothing_is_enabled_by_default():
    fresh = Settings(_env_file=None)
    assert fresh.connectors_enabled == [] and fresh.connectors_allow_scraping is False
    assert not any(is_enabled(n, fresh.connectors_enabled, fresh.connectors_allow_scraping) for n in CATALOGUE)


def test_scraping_needs_both_switches():
    assert not is_enabled("google_scholar", ["google_scholar"], allow_scraping=False)
    assert is_enabled("google_scholar", ["google_scholar"], allow_scraping=True)
    assert not is_enabled("google_scholar", ["openalex"], allow_scraping=True)  # allowing isn't enabling
    assert is_enabled("openalex", ["openalex"], allow_scraping=False)


def test_unknown_names_are_never_enabled_and_are_reported():
    assert not is_enabled("nope", ["nope"], True)
    assert unknown_connectors(["openalex", "nope", "nope2"]) == ["nope", "nope2"]


def test_production_refuses_unknown_connector_names():
    base = dict(
        environment="production",
        session_secret="x" * 40,
        database_url="postgresql+psycopg://u:p@h/db",
        auto_create_schema=False,
        public_origin="https://lab.example.org",
        google_client_id="id",
        google_client_secret="secret",
        google_redirect_uri="https://lab.example.org/api/auth/callback",
        _env_file=None,
    )
    assert Settings(**base, connectors_enabled=["openalex"]).connectors_enabled == ["openalex"]
    with pytest.raises(ValueError, match="unknown connector"):
        Settings(**base, connectors_enabled=["openalx"])


def test_api_lists_access_kind_and_state(settings, monkeypatch):
    monkeypatch.setattr(settings, "connectors_enabled", ["openalex", "google_scholar"])
    rows = {row["name"]: row for row in _client().get("/api/connectors").json()}
    assert rows["openalex"]["access"] == "official_api" and rows["openalex"]["enabled"] is True
    assert rows["google_scholar"]["access"] == "scraping" and rows["google_scholar"]["enabled"] is False
    assert rows["scopus"]["access"] == "licensed" and rows["scopus"]["enabled"] is False
    assert set(rows) == set(CATALOGUE)


def test_enabled_but_unimplemented_is_not_usable(settings, monkeypatch):
    monkeypatch.setattr(settings, "connectors_enabled", ["arxiv"])
    row = next(r for r in _client().get("/api/connectors").json() if r["name"] == "arxiv")
    assert row["enabled"] is True and row["implemented"] is False and row["usable"] is False


def test_an_implemented_connector_is_usable_only_when_enabled(settings, monkeypatch):
    rows = {r["name"]: r for r in _client().get("/api/connectors").json()}
    assert rows["openalex"]["implemented"] is True and rows["openalex"]["usable"] is False
    monkeypatch.setattr(settings, "connectors_enabled", ["openalex"])
    assert next(r for r in _client().get("/api/connectors").json() if r["name"] == "openalex")["usable"] is True


def test_api_requires_sign_in():
    assert _client(signed_in=False).get("/api/connectors").status_code == 401
