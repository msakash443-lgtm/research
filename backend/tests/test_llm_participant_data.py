import json
import types

import httpx
import pytest
from pydantic import SecretStr

from app.agent.llm import LLMConfigurationError, OpenAICompatibleLLM
from app.config import Settings


def _settings(**overrides) -> Settings:
    base = dict(
        llm_api_key=SecretStr("third-party-key"),
        llm_api_base_url="http://third-party.test/v1",
        llm_model="third-party-model",
        local_llm_api_key=None,
        local_llm_api_base_url=None,
        local_llm_model=None,
        participant_data_requires_local_model=True,
    )
    base.update(overrides)
    return Settings(_env_file=None, **base)


def test_for_participant_data_refuses_the_third_party_model_by_default():
    settings = _settings()

    with pytest.raises(LLMConfigurationError, match="locally configured model"):
        OpenAICompatibleLLM.for_participant_data(settings)


def test_for_participant_data_uses_the_local_endpoint_when_configured(monkeypatch):
    settings = _settings(
        local_llm_api_base_url="http://local-model.test/v1",
        local_llm_model="local-model",
        local_llm_timeout_seconds=42,
    )
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "local answer"}}]})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        "app.agent.llm.httpx",
        types.SimpleNamespace(Client=lambda **kw: httpx.Client(transport=transport, **kw), HTTPError=httpx.HTTPError),
    )

    client = OpenAICompatibleLLM.for_participant_data(settings)
    answer = client.complete("system", "user")

    assert answer == "local answer"
    (request,) = requests
    assert str(request.url) == "http://local-model.test/v1/chat/completions"
    # No local API key configured: no Authorization header sent (most local servers need none).
    assert "authorization" not in request.headers
    body = json.loads(request.content)
    assert body["model"] == "local-model"


def test_for_participant_data_falls_back_to_third_party_when_the_switch_is_off(monkeypatch):
    settings = _settings(participant_data_requires_local_model=False)
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "third-party answer"}}]})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        "app.agent.llm.httpx",
        types.SimpleNamespace(Client=lambda **kw: httpx.Client(transport=transport, **kw), HTTPError=httpx.HTTPError),
    )

    client = OpenAICompatibleLLM.for_participant_data(settings)
    answer = client.complete("system", "user")

    assert answer == "third-party answer"
    (request,) = requests
    assert str(request.url) == "http://third-party.test/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer third-party-key"


def test_for_participant_data_still_fails_loudly_with_no_model_configured_at_all():
    settings = _settings(
        participant_data_requires_local_model=False,
        llm_api_key=None,
        llm_api_base_url=None,
        llm_model=None,
    )

    client = OpenAICompatibleLLM.for_participant_data(settings)

    with pytest.raises(LLMConfigurationError, match="not connected to an LLM"):
        client.complete("system", "user")


def test_local_model_settings_must_be_set_together():
    with pytest.raises(ValueError, match="LOCAL_LLM_API_BASE_URL and LOCAL_LLM_MODEL"):
        Settings(_env_file=None, local_llm_api_base_url="http://local.test/v1", local_llm_model=None)

    with pytest.raises(ValueError, match="LOCAL_LLM_API_BASE_URL and LOCAL_LLM_MODEL"):
        Settings(_env_file=None, local_llm_api_base_url=None, local_llm_model="local-model")
