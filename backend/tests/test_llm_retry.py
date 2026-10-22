"""X.109: a temporary LLM failure is retried a few times before the step fails loudly."""

import httpx
import pytest

from app.agent import llm
from app.agent.llm import LLMResponseError, OpenAICompatibleLLM
from app.config import get_settings


def _complete():
    return OpenAICompatibleLLM(get_settings()).complete("system", "user")


def _fail_then_answer(failures):
    """Answer with each failure in turn (a Response, or an exception to raise), then a good reply."""
    remaining = list(failures)

    def handler(request):
        if remaining:
            failure = remaining.pop(0)
            if isinstance(failure, Exception):
                raise failure
            return failure
        return {"choices": [{"message": {"content": "Recovered."}}]}

    return handler


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_a_temporary_http_error_is_retried_and_the_later_answer_is_used(fake_llm, status):
    fake_llm.handler = _fail_then_answer([httpx.Response(status, json={})])

    assert _complete() == "Recovered."
    assert len(fake_llm.requests) == 2


def test_a_connection_failure_or_timeout_is_retried(fake_llm):
    request = httpx.Request("POST", "http://llm.test/v1/chat/completions")
    fake_llm.handler = _fail_then_answer([httpx.ConnectError("refused", request=request), httpx.ReadTimeout("slow", request=request)])

    assert _complete() == "Recovered."
    assert len(fake_llm.requests) == 3


def test_a_failure_that_lasts_past_every_attempt_still_fails_loudly(fake_llm):
    fake_llm.handler = lambda request: httpx.Response(503, json={})

    with pytest.raises(LLMResponseError, match="could not be reached"):
        _complete()
    assert len(fake_llm.requests) == llm.TRANSIENT_ATTEMPTS == 3
    assert fake_llm.sleeps == [1.0, 2.0]  # backoff between attempts, none after the last


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_a_request_the_service_refuses_is_not_retried(fake_llm, status):
    fake_llm.handler = lambda request: httpx.Response(status, json={})

    with pytest.raises(LLMResponseError):
        _complete()
    assert len(fake_llm.requests) == 1
    assert fake_llm.sleeps == []


def test_invalid_json_in_a_success_reply_is_not_retried(fake_llm):
    fake_llm.handler = lambda request: httpx.Response(200, content=b"not json")

    with pytest.raises(LLMResponseError, match="invalid JSON"):
        _complete()
    assert len(fake_llm.requests) == 1


def test_a_numeric_retry_after_is_honoured_up_to_a_cap(fake_llm):
    fake_llm.handler = _fail_then_answer(
        [httpx.Response(429, headers={"Retry-After": "7"}, json={}), httpx.Response(429, headers={"Retry-After": "3600"}, json={})]
    )

    assert _complete() == "Recovered."
    assert fake_llm.sleeps == [7.0, llm.MAX_RETRY_AFTER_SECONDS]


def test_an_unreadable_retry_after_falls_back_to_the_normal_backoff(fake_llm):
    fake_llm.handler = _fail_then_answer([httpx.Response(503, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}, json={})])

    assert _complete() == "Recovered."
    assert fake_llm.sleeps == [1.0]


def test_the_budget_meter_is_checked_once_and_charged_once_for_a_retried_call(fake_llm):
    calls = []

    class Meter:
        def before_call(self, max_tokens=0):
            calls.append("before")

        def after_call(self, model, response, ticket=None):
            calls.append("after")

        def abort_call(self, ticket):
            calls.append("abort")

    fake_llm.handler = _fail_then_answer([httpx.Response(502, json={})])

    assert OpenAICompatibleLLM(get_settings(), meter=Meter()).complete("system", "user") == "Recovered."
    assert calls == ["before", "after"]
