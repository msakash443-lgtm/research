"""Run one workspace AI action (X.31.11): fence the input, ask the model, validate and leak-check the reply.

The only module that calls a model for the side panel. It returns items, and stores nothing: the task handler
turns the items into `AiSuggestion` rows, so a failure here can never leave a half-written suggestion behind.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.agent.llm import OpenAICompatibleLLM
from app.prompt_registry import Prompt, load_prompt
from app.workspace_ai import (
    MAX_SOURCES,
    Item,
    SourceExcerpt,
    WorkspaceAIError,
    ensure_action,
    fence_sources,
    fence_text,
    leak_check,
    parse_reply,
    schema_for,
)


# Module-level (name, version) constants: the evaluation rule (evals/baselines.py) finds prompts by these.
WORKSPACE_CHALLENGE = ("workspace_challenge", 1)
WORKSPACE_FIND_EVIDENCE = ("workspace_find_evidence", 1)
WORKSPACE_CLEARER_SENTENCE = ("workspace_clearer_sentence", 1)
WORKSPACE_ARGUMENT_CHECK = ("workspace_argument_check", 1)


def default_prompt(action: str) -> Prompt:
    ensure_action(action)
    if action == "challenge":
        return load_prompt(*WORKSPACE_CHALLENGE)
    if action == "find_evidence":
        return load_prompt(*WORKSPACE_FIND_EVIDENCE)
    if action == "clearer_sentence":
        return load_prompt(*WORKSPACE_CLEARER_SENTENCE)
    return load_prompt(*WORKSPACE_ARGUMENT_CHECK)


@dataclass(frozen=True)
class RunResult:
    items: list[Item]
    model_id: str
    prompt_version: str


def run_action(
    llm: OpenAICompatibleLLM,
    action: str,
    text: str,
    sources: list[SourceExcerpt] | None = None,
    *,
    prompt: Prompt | None = None,
) -> RunResult:
    """Ask the model for one action on one passage. Raises `WorkspaceAIError` or an `LLM*Error`; never returns guessed output."""
    ensure_action(action)
    prompt = prompt or default_prompt(action)
    fenced = fence_text(text)
    sent_sources = list(sources or [])[:MAX_SOURCES]
    if action == "find_evidence":
        if not sent_sources:
            raise WorkspaceAIError("no_sources", "The project has no source excerpts to compare with.")
        sources_block, excerpt_ids = fence_sources(sent_sources)
        user = prompt.render_user(text=fenced.block, sources=sources_block)
        source_ids = {s.source_id for s in sent_sources}
    else:
        user = prompt.render_user(text=fenced.block)
        excerpt_ids, source_ids = set(), set()
    reply = llm.complete_json(prompt.system, user, schema_for(action))
    items = parse_reply(action, reply, source_ids, excerpt_ids)
    leak_check(items, system_prompt=prompt.system, nonce=fenced.nonce)
    return RunResult(items=items, model_id=llm._model, prompt_version=prompt.ref)
