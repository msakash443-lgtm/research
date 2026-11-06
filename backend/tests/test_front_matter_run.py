from types import SimpleNamespace

import pytest

from app.front_matter import FrontMatterError
from app.front_matter_run import prompt_ref, run


class FakeLLM:
    _model = "test-model"

    def complete_json(self, system, user, schema):
        assert "Saved manuscript context" in user
        assert schema["required"] == ["text"]
        return {"text": "A proposed abstract."}


def test_each_kind_has_a_pinned_prompt():
    assert prompt_ref("abstract") == ("front_matter_abstract", 1)
    assert prompt_ref("keywords") == ("front_matter_keywords", 1)
    assert prompt_ref("highlights") == ("front_matter_highlights", 1)
    assert prompt_ref("cover_letter") == ("front_matter_cover_letter", 1)


def test_runner_returns_provenance_and_validated_text():
    result = run(FakeLLM(), "abstract", "A saved manuscript")
    assert result.text == "A proposed abstract."
    assert result.model_id == "test-model"
    assert result.prompt_version == "front_matter_abstract@1"


def test_runner_refuses_empty_context():
    with pytest.raises(FrontMatterError):
        run(FakeLLM(), "abstract", "")
