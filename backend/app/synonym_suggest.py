"""LLM-assisted synonym *proposals* for one concept block (M1.5.3).

The scholar edits and accepts them; nothing here saves or searches anything. The reply is
schema-validated, then filtered by code: terms already in use (after normalisation), duplicates,
unusable terms and over-long reasons are dropped, and the count is capped. Pure apart from the
`llm` argument.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass

from app.agent.llm import OpenAICompatibleLLM
from app.answer_guard import check_answer
from app.prompt_registry import Prompt, load_prompt
from app.search_query import ConceptBlock, QueryError, is_wildcard, normalise_term

PROMPT = ("synonym_suggestions", 1)
MAX_SUGGESTIONS = 12
MAX_WORDS = 4
MAX_REASON_CHARS = 300

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["suggestions"],
    "properties": {
        "suggestions": {
            "type": "array",
            "maxItems": 40,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["term", "reason"],
                "properties": {"term": {"type": "string", "maxLength": 200}, "reason": {"type": "string", "maxLength": 2000}},
            },
        }
    },
}


class SuggestionError(RuntimeError):
    pass


@dataclass(frozen=True)
class Proposal:
    term: str
    reason: str


@dataclass(frozen=True)
class Proposals:
    items: list[Proposal]
    dropped: int  # how many the model offered that were filtered out
    model_id: str
    prompt_version: str


def _key(term: str) -> str:
    return " ".join(term.casefold().split())


def filter_proposals(block: ConceptBlock, reply: dict) -> tuple[list[Proposal], int]:
    seen = {_key(t) for t in block.terms}
    kept: list[Proposal] = []
    offered = reply.get("suggestions", [])
    for item in offered:
        try:
            term = normalise_term(item["term"])
        except (QueryError, ValueError):
            continue
        if is_wildcard(term) or len(term.split()) > MAX_WORDS or _key(term) in seen or '"' in term:
            continue
        reason = " ".join(str(item.get("reason", "")).split())[:MAX_REASON_CHARS]
        if not reason:
            continue
        seen.add(_key(term))
        kept.append(Proposal(term, reason))
        if len(kept) >= MAX_SUGGESTIONS:
            break
    return kept, len(offered) - len(kept)


def suggest_synonyms(llm: OpenAICompatibleLLM, block: ConceptBlock, question: str | None, *, prompt: Prompt | None = None) -> Proposals:
    """Ask the model for extra terms. Raises an `LLM*Error` or `SuggestionError`; never returns made-up output."""
    prompt = prompt or load_prompt(*PROMPT)
    user = prompt.render_user(
        concept=block.label or ", ".join(block.terms[:3]),
        terms="; ".join(block.terms),
        question=(question or "(not given)").strip()[:500],
    )
    reply = llm.complete_json(prompt.system, user, SCHEMA)
    leaked = check_answer(" ".join(f"{i['term']} {i['reason']}" for i in reply["suggestions"]), system_prompt=prompt.system, nonce=secrets.token_hex(8))
    if leaked:
        raise SuggestionError(f"The model's suggestions were rejected because they repeated {leaked}.")
    kept, dropped = filter_proposals(block, reply)
    return Proposals(kept, dropped, llm._model, prompt.ref)
