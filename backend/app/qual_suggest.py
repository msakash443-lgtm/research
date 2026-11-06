"""AI code suggestions for one qualitative excerpt (M5.5.2, slice 1: pure, no storage, no route).

The excerpt is participant text, so it goes only through `OpenAICompatibleLLM.for_participant_data`, which the caller
passes in. Suggestions are proposals: nothing here changes a codebook. A person accepts a suggestion through
`qual_coding.accept`. A reply that is not the expected JSON is refused loudly, never turned into placeholder codes.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from app.prompt_registry import load_prompt

MAX_SUGGESTIONS = 5  # the v1 prompt text says "at most 5"; a new limit needs a new prompt version
MAX_TOKENS = 600

# Pinned prompt version (X.121): each stored suggestion records this as its prompt_version.
PROMPT = ("qual_code_suggestions", 1)


def prompt_ref() -> str:
    """`name@version` of the pinned prompt, stored with each suggestion."""
    return load_prompt(*PROMPT).ref


class SuggestionError(ValueError):
    """A suggestion request that cannot be made or a reply that cannot be used. The message is safe to show."""


class ChatModel(Protocol):
    def complete(self, system_prompt: str, user_prompt: str, *, max_tokens: int = ...) -> str: ...


@dataclass(frozen=True)
class Suggestion:
    code: str
    is_new: bool
    rationale: str


def build_user_prompt(existing: Mapping[str, str], excerpt: str) -> str:
    """The existing codebook and the excerpt. The excerpt appears here and nowhere else."""
    lines = [f"- {name}: {definition}" if definition else f"- {name}" for name, definition in existing.items()]
    codebook = "\n".join(lines) if lines else "(none yet)"
    return load_prompt(*PROMPT).render_user(codebook=codebook, excerpt=excerpt)


def parse_reply(reply: str, existing: Mapping[str, str]) -> list[Suggestion]:
    try:
        items: Any = json.loads(reply)
    except ValueError as exc:
        raise SuggestionError("The model did not return valid JSON, so no suggestions were made.") from exc
    if not isinstance(items, list):
        raise SuggestionError("The model's reply was not a JSON list, so no suggestions were made.")
    if len(items) > MAX_SUGGESTIONS:
        raise SuggestionError(f"The model suggested {len(items)} codes; at most {MAX_SUGGESTIONS} are allowed, so none were kept.")
    out: list[Suggestion] = []
    for item in items:
        code = item.get("code") if isinstance(item, dict) else None
        if not isinstance(code, str) or not code.strip():
            raise SuggestionError("A suggestion had no code name, so no suggestions were made.")
        rationale = item.get("rationale", "")
        out.append(Suggestion(code.strip(), code.strip() not in existing, rationale if isinstance(rationale, str) else ""))
    return out


def suggest_codes(llm: ChatModel, existing: Mapping[str, str], excerpt: str) -> list[Suggestion]:
    """Ask the model for codes for one excerpt. `llm` must be the participant-data adapter."""
    if not (excerpt or "").strip():
        raise SuggestionError("The excerpt is blank; nothing to code.")
    reply = llm.complete(load_prompt(*PROMPT).system, build_user_prompt(existing, excerpt), max_tokens=MAX_TOKENS)
    return parse_reply(reply, existing)
