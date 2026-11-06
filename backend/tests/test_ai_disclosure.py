from app.ai_disclosure import NOT_AN_AUTHOR, AiAuditAction, generate


def test_no_ai_events_says_so_and_still_states_ai_is_not_an_author():
    text = generate([])
    assert "No AI-assisted actions are recorded" in text
    assert NOT_AN_AUTHOR in text


def test_only_human_actions_with_no_model_id_count_as_no_ai_use():
    events = [AiAuditAction(action="screening.decided", model_id=None, prompt_version=None)]
    text = generate(events)
    assert "No AI-assisted actions are recorded" in text


def test_ai_events_are_summarised_by_model_and_prompt_version():
    events = [
        AiAuditAction(action="research_run.answered", model_id="gpt-test", prompt_version="evidence_synthesis@2"),
        AiAuditAction(action="research_run.answered", model_id="gpt-test", prompt_version="evidence_synthesis@2"),
        AiAuditAction(action="screening.ai_decided", model_id="gpt-test", prompt_version="prescreen@1"),
    ]
    text = generate(events)
    assert "3 action(s)" in text
    assert "research_run.answered" in text and "screening.ai_decided" in text
    assert "gpt-test" in text
    assert "evidence_synthesis@2" in text and "prescreen@1" in text
    assert NOT_AN_AUTHOR in text


def test_multiple_models_are_each_listed():
    events = [
        AiAuditAction(action="a", model_id="model-a", prompt_version=None),
        AiAuditAction(action="b", model_id="model-b", prompt_version="v1"),
    ]
    text = generate(events)
    assert "model-a" in text and "model-b" in text


def test_output_is_deterministic_regardless_of_input_order():
    first = [
        AiAuditAction(action="a", model_id="model-a", prompt_version="v1"),
        AiAuditAction(action="b", model_id="model-b", prompt_version="v2"),
    ]
    second = list(reversed(first))
    assert generate(first) == generate(second)
