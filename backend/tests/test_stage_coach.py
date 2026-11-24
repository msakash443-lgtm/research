from types import SimpleNamespace

import pytest

from app.stage_coach import StageCoachError, _context_text, coach


class StubLLM:
    _model = "stub-model"

    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def complete_json(self, system, user, schema):
        self.calls.append((system, user, schema))
        return self.reply


def _context():
    return {
        "stage": "extracted",
        "research_type": "literature_review",
        "next": [{"stage": "synthesized", "gate": "G4", "ready": False}],
        "conditions": [{"id": "EC-5.1", "met": False, "message": "Verify every extraction."}],
    }


def test_stage_coach_sends_only_authoritative_stage_context_and_returns_provenance():
    llm = StubLLM({"explanation": "Verify the remaining extraction.", "actions": [{"text": "Review it against the PDF.", "condition_id": "EC-5.1"}]})
    result = coach(llm, _context())
    assert result.explanation == "Verify the remaining extraction."
    assert result.actions == [{"text": "Review it against the PDF.", "condition_id": "EC-5.1"}]
    assert result.model_id == "stub-model"
    assert result.prompt_version == "stage_coach@1"
    assert "EC-5.1" in llm.calls[0][1]
    assert "Verify every extraction." in llm.calls[0][1]


def test_stage_coach_rejects_actions_for_unknown_conditions():
    llm = StubLLM({"explanation": "Do something.", "actions": [{"text": "Approve G4.", "condition_id": "G4"}]})
    with pytest.raises(StageCoachError, match="unknown condition"):
        coach(llm, _context())


def test_stage_coach_context_excludes_source_text():
    text = _context_text(_context())
    assert "PDF" not in text
    assert "EC-5.1" in text
