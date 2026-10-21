import json

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.processors import processor_register


def _names(settings):
    return {p["id"]: p for p in processor_register(settings)}


def test_a_default_install_lists_no_outside_processor():
    assert processor_register(Settings(_env_file=None)) == []


def test_each_switched_on_service_is_listed_with_its_host_only():
    settings = Settings(
        _env_file=None,
        llm_api_base_url="https://user:pw@llm.example.com/v1/secret-path?key=abc",
        llm_api_key="sk-super-secret",
        connectors_enabled=["openalex", "scopus"],
        connector_contact_email="lab@example.org",
        arc_retrieval_enabled=True,
        arc_retrieval_base_url="https://arc.example.net/api",
        arc_retrieval_token="arc-token-value",
    )

    register = _names(settings)

    assert register["llm"]["host"] == "llm.example.com"
    assert register["arc"]["host"] == "arc.example.net"
    assert {"connector:openalex", "connector:scopus"} <= set(register)
    assert "lab@example.org" in register["connector:openalex"]["data_sent"]
    assert register["connector:scopus"]["access"] == "licensed"
    dumped = json.dumps(register)
    for secret in ("sk-super-secret", "arc-token-value", "pw@", "secret-path", "abc"):
        assert secret not in dumped


def test_a_connector_that_is_not_enabled_is_not_listed():
    register = _names(Settings(_env_file=None, connectors_enabled=["crossref"]))

    assert set(register) == {"connector:crossref"}


def test_the_notice_endpoint_needs_sign_in_and_returns_notice_and_register():
    client = TestClient(app)
    assert client.get("/api/privacy/processors").status_code == 401

    client.post("/api/auth/development/login", json={"email": "privacy@example.com", "display_name": "P"})
    response = client.get("/api/privacy/processors")

    assert response.status_code == 200
    body = response.json()
    assert body["notice"] and isinstance(body["processors"], list)


def test_google_forms_is_listed_once_its_access_token_is_set():
    assert "google_forms" not in _names(Settings(_env_file=None))

    register = _names(Settings(_env_file=None, google_forms_access_token="forms-token-value"))

    assert register["google_forms"]["host"] == "forms.googleapis.com"
    assert "forms-token-value" not in json.dumps(register)


def test_open_access_pdf_hosts_are_listed_when_unpaywall_or_arxiv_is_on():
    assert "fulltext_hosts" not in _names(Settings(_env_file=None, connectors_enabled=["crossref"]))

    for name in ("unpaywall", "arxiv"):
        register = _names(Settings(_env_file=None, connectors_enabled=[name]))
        assert "PDF" in register["fulltext_hosts"]["data_sent"]


def test_a_local_model_is_listed_only_when_it_may_sit_on_a_public_host():
    private = Settings(_env_file=None, local_llm_api_base_url="http://10.0.0.5:8000/v1", local_llm_model="m")
    assert "local_llm" not in _names(private)

    public = Settings(
        _env_file=None, local_llm_api_base_url="https://key:pw@models.example.org/v1", local_llm_model="m",
        local_llm_allow_public_host=True,
    )
    register = _names(public)

    assert register["local_llm"]["host"] == "models.example.org"
    assert "pw@" not in json.dumps(register)


def test_the_notice_does_not_point_to_a_screen_that_does_not_exist():
    from app.processors import NOTICE

    assert "account settings" not in NOTICE
