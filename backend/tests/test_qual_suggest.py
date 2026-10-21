import json

import pytest

from app import qual_coding as qc
from app import qual_suggest as qs

EXCERPT = "My manager never explains decisions, so I stopped asking."


class FakeLLM:
    def __init__(self, reply: str):
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_prompt: str, *, max_tokens: int = 1800) -> str:
        self.calls.append((system_prompt, user_prompt))
        return self.reply


def test_excerpt_goes_only_to_the_user_prompt_not_the_system_prompt():
    llm = FakeLLM("[]")
    qs.suggest_codes(llm, {"trust": "belief in others"}, EXCERPT)
    system, user = llm.calls[0]
    assert EXCERPT not in system
    assert EXCERPT in user


def test_existing_codes_are_offered_so_the_model_can_reuse_them():
    llm = FakeLLM("[]")
    qs.suggest_codes(llm, {"trust": "belief in others"}, EXCERPT)
    _, user = llm.calls[0]
    assert "trust" in user


def test_a_valid_reply_becomes_suggestions_marked_new_or_existing():
    reply = json.dumps([
        {"code": "trust", "rationale": "unexplained decisions"},
        {"code": "silence", "rationale": "stopped asking"},
    ])
    out = qs.suggest_codes(FakeLLM(reply), {"trust": "belief in others"}, EXCERPT)
    assert [(s.code, s.is_new) for s in out] == [("trust", False), ("silence", True)]
    assert out[1].rationale == "stopped asking"


def test_a_reply_that_is_not_json_fails_loudly_and_gives_no_suggestions():
    # AI failures fail loudly, with no placeholder output (plan rule, section 1.6)
    with pytest.raises(qs.SuggestionError, match="valid JSON"):
        qs.suggest_codes(FakeLLM("Sure! Here are some codes..."), {}, EXCERPT)


def test_a_reply_that_is_not_a_list_of_code_objects_is_refused():
    with pytest.raises(qs.SuggestionError):
        qs.suggest_codes(FakeLLM('{"code": "trust"}'), {}, EXCERPT)
    with pytest.raises(qs.SuggestionError):
        qs.suggest_codes(FakeLLM('[{"rationale": "no code name"}]'), {}, EXCERPT)


def test_more_than_five_suggestions_are_refused_not_truncated():
    reply = json.dumps([{"code": f"c{i}", "rationale": "x"} for i in range(6)])
    with pytest.raises(qs.SuggestionError, match="at most 5"):
        qs.suggest_codes(FakeLLM(reply), {}, EXCERPT)


def test_suggesting_never_changes_the_codebook():
    book = qc.new_codebook({"trust": "belief in others"})
    before = json.dumps(book, sort_keys=True)
    qs.suggest_codes(FakeLLM(json.dumps([{"code": "silence", "rationale": "x"}])), book["codes"], EXCERPT)
    assert json.dumps(book, sort_keys=True) == before


def test_a_blank_excerpt_is_refused_before_any_model_call():
    llm = FakeLLM("[]")
    with pytest.raises(qs.SuggestionError):
        qs.suggest_codes(llm, {}, "   ")
    assert llm.calls == []


def test_the_request_uses_the_pinned_registry_prompt():
    # X.121: the wording lives in a versioned prompt file, so a stored prompt_version names the exact text sent.
    from app.prompt_registry import load_prompt

    prompt = load_prompt(*qs.PROMPT)
    llm = FakeLLM("[]")
    qs.suggest_codes(llm, {"trust": "belief in others"}, EXCERPT)
    system, user = llm.calls[0]
    assert system == prompt.system
    assert user == prompt.render_user(codebook="- trust: belief in others", excerpt=EXCERPT)
    assert f'at most {qs.MAX_SUGGESTIONS} codes' in prompt.system
