import jsonschema
import pytest

from app.output_schemas import EVIDENCE_SYNTHESIS_SCHEMA, with_abstention

OK = {"answer": "Remote work raised output [S1].", "confidence": 0.7, "insufficient_evidence": {"insufficient": False, "reason": ""}}
ABSTAIN = {"answer": "", "confidence": 0.0, "insufficient_evidence": {"insufficient": True, "reason": "No source mentions output."}}


def _valid(reply):
    try:
        jsonschema.Draft202012Validator(EVIDENCE_SYNTHESIS_SCHEMA).validate(reply)
        return True
    except jsonschema.ValidationError:
        return False


def test_an_answer_with_a_confidence_is_accepted():
    assert _valid(OK)


def test_an_abstention_with_a_reason_and_no_answer_is_accepted():
    assert _valid(ABSTAIN)


@pytest.mark.parametrize("bad", [
    {k: v for k, v in OK.items() if k != "confidence"},
    {k: v for k, v in OK.items() if k != "insufficient_evidence"},
    {**OK, "confidence": 1.5},
    {**OK, "answer": ""},
    {**OK, "answer": None},
    {**OK, "insufficient_evidence": {"insufficient": False, "reason": "but unsure"}},
    {**ABSTAIN, "insufficient_evidence": {"insufficient": True, "reason": "   "}},
    {**ABSTAIN, "answer": "I think it helped anyway."},
    {**OK, "extra": "x"},
])
def test_a_reply_that_breaks_the_answer_or_abstain_rules_is_rejected(bad):
    assert not _valid(bad)


def test_with_abstention_does_not_change_the_schema_it_was_given():
    base = {"type": "object", "required": ["a"], "properties": {"a": {"type": "string"}}}
    snapshot = repr(base)
    with_abstention(base, answer_fields=["a"])
    assert repr(base) == snapshot


def test_with_abstention_refuses_a_bad_definition():
    with pytest.raises(ValueError):
        with_abstention({"type": "object", "properties": {}}, answer_fields=["missing"])
    with pytest.raises(ValueError):
        with_abstention({"type": "array"}, answer_fields=["a"])
