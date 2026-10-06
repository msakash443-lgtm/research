import json

import pytest

from app.agent.llm import LLMConfigurationError, LLMResponseError, LLMSchemaError, OpenAICompatibleLLM
from app.config import get_settings

SCHEMA = {
    "type": "object",
    "properties": {"decision": {"enum": ["include", "exclude"]}, "reason": {"type": "string", "minLength": 1}},
    "required": ["decision", "reason"],
    "additionalProperties": False,
}
GOOD = {"decision": "include", "reason": "Matches the criteria."}


def _reply(text):
    return {"choices": [{"message": {"content": text}}]}


def _script(fake_llm, *replies):
    queue = list(replies)
    fake_llm.handler = lambda request: _reply(queue.pop(0))


def _call(**kwargs):
    return OpenAICompatibleLLM(get_settings()).complete_json("sys", "user", SCHEMA, **kwargs)


def test_valid_reply_is_returned_parsed(fake_llm):
    _script(fake_llm, json.dumps(GOOD))
    assert _call() == GOOD
    assert len(fake_llm.requests) == 1


def test_json_fence_is_tolerated(fake_llm):
    _script(fake_llm, "```json\n" + json.dumps(GOOD) + "\n```")
    assert _call() == GOOD


@pytest.mark.parametrize(
    "bad",
    [
        "not json at all",
        json.dumps({"decision": "maybe", "reason": "x"}),  # enum violation
        json.dumps({"decision": "include"}),  # missing key
        json.dumps({**GOOD, "extra": 1}),  # additional property
        json.dumps([GOOD]),  # wrong top-level type
    ],
)
def test_violation_is_rejected_then_retried(fake_llm, bad):
    _script(fake_llm, bad, json.dumps(GOOD))
    assert _call() == GOOD
    assert len(fake_llm.requests) == 2
    retry_user = json.loads(fake_llm.requests[1].content)["messages"][1]["content"]
    assert "previous reply was rejected" in retry_user


def test_fails_after_max_attempts_without_defaults(fake_llm):
    _script(fake_llm, "nope", "nope", "nope", json.dumps(GOOD))
    with pytest.raises(LLMSchemaError) as info:
        _call()
    assert len(fake_llm.requests) == 3  # setting default; the 4th (valid) reply is never requested
    assert "3 attempts" in str(info.value)
    assert isinstance(info.value, LLMResponseError)
    assert "nope" not in str(info.value)  # the invalid reply is not echoed


def test_max_attempts_can_be_overridden(fake_llm):
    _script(fake_llm, "nope", json.dumps(GOOD))
    with pytest.raises(LLMSchemaError):
        _call(max_attempts=1)
    assert len(fake_llm.requests) == 1


def test_http_failure_is_not_retried_as_a_schema_problem(fake_llm):
    import httpx

    fake_llm.handler = lambda request: httpx.Response(500, json={})
    with pytest.raises(LLMResponseError) as info:
        _call()
    assert not isinstance(info.value, LLMSchemaError)
    assert len(fake_llm.requests) == 1


def test_bad_schema_or_attempt_count_fails_before_any_call(fake_llm):
    import jsonschema

    with pytest.raises(jsonschema.SchemaError):
        OpenAICompatibleLLM(get_settings()).complete_json("s", "u", {"type": "nonsense"})
    with pytest.raises(LLMConfigurationError):
        _call(max_attempts=0)
    assert fake_llm.requests == []
