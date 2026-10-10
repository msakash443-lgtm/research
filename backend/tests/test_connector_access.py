import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.connectors.access import CATALOGUE, AccessKind, is_enabled, unknown_connectors
from app.connectors import factory
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
    # API-key connectors fail at start-up when enabled without their credentials.
    with pytest.raises(ValueError, match="CORE_API_KEY"):
        Settings(**base, connectors_enabled=["core"])
    assert Settings(**base, connectors_enabled=["core"], core_api_key="k").connectors_enabled == ["core"]
    for connector, key_name in (
        ("scopus", "SCOPUS_API_KEY"),
        ("web_of_science", "WEB_OF_SCIENCE_API_KEY"),
        ("ieee_xplore", "IEEE_XPLORE_API_KEY"),
    ):
        with pytest.raises(ValueError, match=key_name):
            Settings(**base, connectors_enabled=[connector])
    assert Settings(**base, connectors_enabled=["scopus"], scopus_api_key="k").connectors_enabled == ["scopus"]


def test_api_lists_access_kind_and_state(settings, monkeypatch):
    monkeypatch.setattr(settings, "connectors_enabled", ["openalex", "google_scholar"])
    rows = {row["name"]: row for row in _client().get("/api/connectors").json()}
    assert rows["openalex"]["access"] == "official_api" and rows["openalex"]["enabled"] is True
    assert rows["google_scholar"]["access"] == "scraping" and rows["google_scholar"]["enabled"] is False
    assert rows["scopus"]["access"] == "licensed" and rows["scopus"]["enabled"] is False
    assert set(rows) == set(CATALOGUE)


def test_licensed_connectors_are_usable_only_when_implemented_and_enabled(settings, monkeypatch):
    monkeypatch.setattr(settings, "connectors_enabled", ["scopus"])
    row = next(r for r in _client().get("/api/connectors").json() if r["name"] == "scopus")
    assert row["enabled"] is True and row["implemented"] is True and row["usable"] is True


def test_an_implemented_connector_is_usable_only_when_enabled(settings, monkeypatch):
    rows = {r["name"]: r for r in _client().get("/api/connectors").json()}
    assert rows["openalex"]["implemented"] is True and rows["openalex"]["usable"] is False
    monkeypatch.setattr(settings, "connectors_enabled", ["openalex"])
    assert next(r for r in _client().get("/api/connectors").json() if r["name"] == "openalex")["usable"] is True


def test_licensed_connectors_are_searchable_and_factory_wired(monkeypatch):
    configured = Settings(
        _env_file=None,
        scopus_api_key="scopus-key",
        web_of_science_api_key="wos-key",
        ieee_xplore_api_key="ieee-key",
    )
    monkeypatch.setattr(factory, "get_settings", lambda: configured)
    assert {"scopus", "web_of_science", "ieee_xplore"} <= set(factory.SEARCHABLE)
    assert factory.build_connector("scopus").name == "scopus"
    assert factory.build_connector("web_of_science").name == "web_of_science"
    assert factory.build_connector("ieee_xplore").name == "ieee_xplore"


def test_api_requires_sign_in():
    assert _client(signed_in=False).get("/api/connectors").status_code == 401
