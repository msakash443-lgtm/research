import os

# Set this before importing app modules. Tests must never reuse a developer's
# DATABASE_URL from .env, because the fixture below drops all test tables.
os.environ["DATABASE_URL"] = "sqlite+pysqlite://"
os.environ["ENVIRONMENT"] = "development"
os.environ["AUTO_CREATE_SCHEMA"] = "true"
os.environ["RUN_RESEARCH_INLINE"] = "true"
os.environ["SESSION_SECRET"] = "test-session-secret-that-is-long-enough"

from app.config import Settings

Settings.model_config["env_file"] = None

import pytest
from pydantic import SecretStr

from app.database import Base, engine
from tests.llm_replies import chat_reply


@pytest.fixture(autouse=True)
def isolated_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


class FakeLLMEndpoint:
    """Stands in for the HTTP endpoint behind OpenAICompatibleLLM.

    The real adapter code still runs (request building, response parsing, error mapping);
    only the network is replaced. Set `handler` to change the response.
    """

    def __init__(self):
        self.requests = []
        self.handler = lambda request: chat_reply("Fake answer [S1].")

    def respond(self, request):
        import httpx

        self.requests.append(request)
        result = self.handler(request)
        if isinstance(result, httpx.Response):
            return result
        return httpx.Response(200, json=result)


@pytest.fixture
def fake_llm(monkeypatch):
    """Configure an LLM and route its HTTP calls to an in-process fake."""
    import types

    import httpx

    from app.agent import llm
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "llm_api_key", SecretStr("test-key"))
    monkeypatch.setattr(settings, "llm_api_base_url", "http://llm.test/v1")
    monkeypatch.setattr(settings, "llm_model", "fake-model")

    endpoint = FakeLLMEndpoint()
    transport = httpx.MockTransport(endpoint.respond)

    def client_factory(**kwargs):
        return httpx.Client(transport=transport, **kwargs)

    monkeypatch.setattr(llm, "httpx", types.SimpleNamespace(Client=client_factory, HTTPError=httpx.HTTPError))
    return endpoint
