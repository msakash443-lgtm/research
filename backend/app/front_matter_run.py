"""Model-call boundary for M6.10 front matter proposals."""

from __future__ import annotations

from dataclasses import dataclass

from app.agent.llm import OpenAICompatibleLLM
from app.front_matter import FrontMatterError, OUTPUT_KINDS, canonical_text
from app.prompt_registry import load_prompt


FRONT_MATTER_ABSTRACT_PROMPT = ("front_matter_abstract", 1)
FRONT_MATTER_KEYWORDS_PROMPT = ("front_matter_keywords", 1)
FRONT_MATTER_HIGHLIGHTS_PROMPT = ("front_matter_highlights", 1)
FRONT_MATTER_COVER_LETTER_PROMPT = ("front_matter_cover_letter", 1)


@dataclass(frozen=True)
class FrontMatterResult:
    kind: str
    text: str
    model_id: str
    prompt_version: str


def prompt_ref(kind: str) -> tuple[str, int]:
    if kind == "abstract":
        return FRONT_MATTER_ABSTRACT_PROMPT
    if kind == "keywords":
        return FRONT_MATTER_KEYWORDS_PROMPT
    if kind == "highlights":
        return FRONT_MATTER_HIGHLIGHTS_PROMPT
    if kind == "cover_letter":
        return FRONT_MATTER_COVER_LETTER_PROMPT
    raise FrontMatterError("unknown front-matter output kind")


def schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["text"],
        "properties": {"text": {"type": "string", "minLength": 1, "maxLength": 20000}},
    }


def run(llm: OpenAICompatibleLLM, kind: str, context: str) -> FrontMatterResult:
    if not isinstance(context, str) or not context.strip():
        raise FrontMatterError("manuscript context is empty")
    if kind == "abstract":
        prompt = load_prompt(*FRONT_MATTER_ABSTRACT_PROMPT)
    elif kind == "keywords":
        prompt = load_prompt(*FRONT_MATTER_KEYWORDS_PROMPT)
    elif kind == "highlights":
        prompt = load_prompt(*FRONT_MATTER_HIGHLIGHTS_PROMPT)
    elif kind == "cover_letter":
        prompt = load_prompt(*FRONT_MATTER_COVER_LETTER_PROMPT)
    else:
        raise FrontMatterError("unknown front-matter output kind")
    reply = llm.complete_json(prompt.system, prompt.render_user(context=context[:24000]), schema())
    text = canonical_text(kind, reply.get("text") if isinstance(reply, dict) else None)
    return FrontMatterResult(kind=kind, text=text, model_id=llm._model, prompt_version=prompt.ref)
