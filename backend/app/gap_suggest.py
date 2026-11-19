"""LLM-assisted gap and research-question *proposals* (X.192). Pure apart from the `llm` argument.

Input is only the deterministic X.190 candidates plus the fenced text of verified project sources. The reply is
schema-validated and then checked by code: a gap must cite at least two distinct sources, a question at least one,
and every cited id must be one of the verified sources the model was shown. A proposal that fails is not stored
and is not dropped silently: it comes back in `rejected` with the reason. Nothing here saves or accepts anything;
a person accepts through `POST …/gaps` / `POST …/research-questions`.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any

from app.agent.llm import OpenAICompatibleLLM
from app.answer_guard import check_answer
from app.gap_miner import GAP_TYPES
from app.prompt_registry import Prompt, load_prompt
from app.untrusted_text import clean_untrusted

GAP_PROMPT = ("gap_suggestions", 1)
RQ_PROMPT = ("rq_suggestions", 1)
MAX_CANDIDATES = 15
MAX_SOURCES = 25
MAX_ABSTRACT_CHARS = 1200
MAX_CANDIDATE_CHARS = 400
MAX_PROPOSALS = 10
MIN_GAP_SOURCES = 2
MIN_QUESTION_SOURCES = 1

_STRING_LIST = {"type": "array", "maxItems": 20, "items": {"type": "string", "maxLength": 100}}
GAP_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["gaps"],
    "properties": {"gaps": {"type": "array", "maxItems": 30, "items": {
        "type": "object", "additionalProperties": False, "required": ["type", "statement", "source_ids", "reason"],
        "properties": {"type": {"type": "string", "maxLength": 30}, "statement": {"type": "string", "maxLength": 3000},
                       "source_ids": _STRING_LIST, "reason": {"type": "string", "maxLength": 2000}},
    }}},
}
RQ_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["questions"],
    "properties": {"questions": {"type": "array", "maxItems": 30, "items": {
        "type": "object", "additionalProperties": False, "required": ["text", "type", "source_ids", "reason"],
        "properties": {"text": {"type": "string", "maxLength": 3000}, "type": {"type": "string", "maxLength": 30},
                       "source_ids": _STRING_LIST, "reason": {"type": "string", "maxLength": 2000}},
    }}},
}


class SuggestionError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceView:
    id: str
    title: str
    year: int | None
    abstract: str | None


@dataclass(frozen=True)
class GapProposal:
    type: str
    statement: str
    source_ids: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class QuestionProposal:
    text: str
    type: str
    source_ids: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class Rejected:
    summary: str  # what the model proposed, shortened
    reason: str


@dataclass(frozen=True)
class Proposals:
    items: list[Any]
    rejected: list[Rejected]
    model_id: str
    prompt_version: str


def _clean(text: str, limit: int) -> str:
    return clean_untrusted(text, limit).text


def render_candidates(candidates: list[dict[str, Any]]) -> str:
    lines = [
        f"{n}. [{c['kind']}, {c['type']}] {_clean(c['reason'], MAX_CANDIDATE_CHARS)}"
        for n, c in enumerate(candidates[:MAX_CANDIDATES], 1)
    ]
    return "\n".join(lines) or "(none)"


def render_sources(sources: list[SourceView], nonce: str) -> str:
    blocks = []
    for source in sources[:MAX_SOURCES]:
        abstract = _clean(source.abstract, MAX_ABSTRACT_CHARS) if source.abstract else "(no abstract)"
        blocks.append(
            f"<<<SOURCE id={source.id} fence={nonce}>>>\n"
            f"Title: {_clean(source.title, 300)}\nYear: {source.year or 'unknown'}\nAbstract: {abstract}\n"
            f"<<<END SOURCE fence={nonce}>>>"
        )
    return "\n\n".join(blocks)


def _validate_ids(raw: list[str], shown: set[str], minimum: int) -> tuple[tuple[str, ...], str | None]:
    ids = tuple(dict.fromkeys(item.strip() for item in raw))
    unknown = [item for item in ids if item not in shown]
    if unknown:
        return ids, f"cites a source id that is not one of the verified project sources shown: {unknown[0]}"
    if len(ids) < minimum:
        return ids, f"cites {len(ids)} distinct verified source(s); at least {minimum} required"
    return ids, None


def _call(llm: OpenAICompatibleLLM, prompt: Prompt, schema: dict, key: str, question: str | None,
          candidates: list[dict[str, Any]], sources: list[SourceView]) -> tuple[list[dict], str, str]:
    nonce = secrets.token_hex(8)
    user = prompt.render_user(
        question=_clean(question or "(not given)", 500), candidates=render_candidates(candidates),
        sources=render_sources(sources, nonce),
    )
    reply = llm.complete_json(prompt.system, user, schema)
    items = reply[key]
    text = " ".join(f"{i.get('statement', i.get('text', ''))} {i['reason']}" for i in items)
    leaked = check_answer(text, system_prompt=prompt.system, nonce=nonce)
    if leaked:
        raise SuggestionError(f"The model's suggestions were rejected because they repeated {leaked}.")
    return items, llm._model, prompt.ref


def suggest_gaps(llm: OpenAICompatibleLLM, question: str | None, candidates: list[dict[str, Any]],
                 sources: list[SourceView]) -> Proposals:
    """Raises an `LLM*Error` or `SuggestionError`; never returns made-up output."""
    items, model_id, ref = _call(llm, load_prompt(*GAP_PROMPT), GAP_SCHEMA, "gaps", question, candidates, sources)
    shown = {s.id for s in sources[:MAX_SOURCES]}
    kept: list[GapProposal] = []
    rejected: list[Rejected] = []
    for item in items:
        statement = " ".join(item["statement"].split())
        reason = " ".join(item["reason"].split())
        ids, problem = _validate_ids(item["source_ids"], shown, MIN_GAP_SOURCES)
        if problem is None and item["type"] not in GAP_TYPES:
            problem = f"type must be one of: {', '.join(sorted(GAP_TYPES))}"
        if problem is None and (not statement or not reason):
            problem = "statement and reason are both required"
        if problem:
            rejected.append(Rejected(statement[:200] or "(empty statement)", problem))
        elif len(kept) < MAX_PROPOSALS:
            kept.append(GapProposal(item["type"], statement, ids, reason))
    return Proposals(kept, rejected, model_id, ref)


def suggest_questions(llm: OpenAICompatibleLLM, question: str | None, candidates: list[dict[str, Any]],
                      sources: list[SourceView]) -> Proposals:
    items, model_id, ref = _call(llm, load_prompt(*RQ_PROMPT), RQ_SCHEMA, "questions", question, candidates, sources)
    shown = {s.id for s in sources[:MAX_SOURCES]}
    kept: list[QuestionProposal] = []
    rejected: list[Rejected] = []
    for item in items:
        text = " ".join(item["text"].split())
        reason = " ".join(item["reason"].split())
        qtype = item["type"].strip().lower()
        ids, problem = _validate_ids(item["source_ids"], shown, MIN_QUESTION_SOURCES)
        if problem is None and (not text.endswith("?") or text.count("?") != 1):
            problem = "a research question must be a single interrogative sentence ending in one question mark"
        if problem is None and (not qtype or not reason):
            problem = "type and reason are both required"
        if problem:
            rejected.append(Rejected(text[:200] or "(empty question)", problem))
        elif len(kept) < MAX_PROPOSALS:
            kept.append(QuestionProposal(text, qtype, ids, reason))
    return Proposals(kept, rejected, model_id, ref)
