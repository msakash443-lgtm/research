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
        local_llm_api_base_url="http://ollama:11434/v1",
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
    assert str(request.url) == "http://ollama:11434/v1/chat/completions"
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


# ---- the "local" endpoint must really be local (M0.8.8, Decisions log 2026-10-08) -------------------------

@pytest.mark.parametrize("url", [
    "http://127.0.0.1:11434/v1",
    "http://[::1]:8000/v1",
    "http://localhost:1234/v1",
    "https://10.0.0.5/v1",
    "http://172.20.1.1:8000/v1",
    "http://192.168.1.9/v1",
    "http://169.254.1.1/v1",
    "http://[fd00::1]/v1",
    "http://[::ffff:192.168.0.1]/v1",
    "http://ollama:11434/v1",
    "http://gpu-box.internal/v1",
    "http://model.local/v1",
    "http://llm.localhost/v1",
])
def test_a_local_or_private_endpoint_is_accepted(url):
    assert Settings(_env_file=None, local_llm_api_base_url=url, local_llm_model="m").local_llm_api_base_url == url


@pytest.mark.parametrize("url", [
    "https://api.openai.com/v1",
    "https://my-lab-gpu.example.edu/v1",
    "http://local-model.test/v1",
    "http://8.8.8.8/v1",
    "http://[2001:4860:4860::8888]/v1",
    "http://[::ffff:8.8.8.8]/v1",
    "http://2130706433/v1",
    "http://0x08080808/v1",
    "ftp://localhost/v1",
    "not a url",
])
def test_a_public_or_unclear_endpoint_is_refused_by_default(url):
    with pytest.raises(ValueError, match="LOCAL_LLM_ALLOW_PUBLIC_HOST"):
        Settings(_env_file=None, local_llm_api_base_url=url, local_llm_model="m")


def test_a_public_self_hosted_endpoint_needs_the_explicit_override():
    settings = Settings(
        _env_file=None, local_llm_api_base_url="https://my-lab-gpu.example.edu/v1", local_llm_model="m",
        local_llm_allow_public_host=True,
    )
    assert OpenAICompatibleLLM.for_participant_data(settings)._api_base_url == "https://my-lab-gpu.example.edu/v1"


# ---- participant-data calls count toward the project token budget (M0.8.8 / M0.9.1) ----------------------

class _Meter:
    def __init__(self, refuse=False):
        self.refuse, self.events = refuse, []

    def before_call(self):
        self.events.append("before")
        if self.refuse:
            raise LLMConfigurationError("budget used")

    def after_call(self, model, data):
        self.events.append(("after", model))


def _mock_http(monkeypatch, requests):
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}], "usage": {"total_tokens": 5}})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        "app.agent.llm.httpx",
        types.SimpleNamespace(Client=lambda **kw: httpx.Client(transport=transport, **kw), HTTPError=httpx.HTTPError),
    )


@pytest.mark.parametrize("overrides, model", [
    ({"local_llm_api_base_url": "http://ollama:11434/v1", "local_llm_model": "local-model"}, "local-model"),
    ({"participant_data_requires_local_model": False}, "third-party-model"),
])
def test_participant_data_calls_are_metered(monkeypatch, overrides, model):
    requests, meter = [], _Meter()
    _mock_http(monkeypatch, requests)
    OpenAICompatibleLLM.for_participant_data(_settings(**overrides), meter=meter).complete("system", "user")
    assert meter.events == ["before", ("after", model)] and len(requests) == 1


def test_a_participant_data_call_over_budget_is_never_sent(monkeypatch):
    requests = []
    _mock_http(monkeypatch, requests)
    llm = OpenAICompatibleLLM.for_participant_data(
        _settings(local_llm_api_base_url="http://ollama:11434/v1", local_llm_model="local-model"), meter=_Meter(refuse=True)
    )
    with pytest.raises(LLMConfigurationError, match="budget used"):
        llm.complete("system", "user")
    assert requests == []
