"""Run the section drafting request (X.46.8 / M6.1.a): fence the outline, ask the model, validate the reply.

Mirrors `workspace_ai_run.py`. The only module that calls a model for section drafting. It returns the draft
text and stores nothing: the task handler (`section_draft_task.py`) turns it into one `AiSuggestion` row, so a
failure here can never leave a half-written suggestion behind.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.agent.llm import OpenAICompatibleLLM
from app.prompt_registry import Prompt, load_prompt
from app.section_draft import OutlineClaim, ensure_outline, fence_outline, leak_check, parse_reply, schema

# Module-level (name, version) constant: the evaluation rule (evals/baselines.py) finds this prompt by it.
SECTION_DRAFT_PROMPT = ("section_draft", 1)


def default_prompt() -> Prompt:
    return load_prompt(*SECTION_DRAFT_PROMPT)


@dataclass(frozen=True)
class DraftResult:
    draft: str
    model_id: str
    prompt_version: str


def run_draft(llm: OpenAICompatibleLLM, claims: list[OutlineClaim], *, prompt: Prompt | None = None) -> DraftResult:
    """Ask the model for a draft paragraph from one section's claim outline. Raises `SectionDraftError` or an
    `LLM*Error`; never returns guessed output."""
    prompt = prompt or default_prompt()
    sent = ensure_outline(claims)
    fenced = fence_outline(sent)
    user = prompt.render_user(outline=fenced.block)
    reply = llm.complete_json(prompt.system, user, schema())
    draft = parse_reply(reply)
    leak_check(draft, system_prompt=prompt.system, nonce=fenced.nonce)
    return DraftResult(draft=draft, model_id=llm._model, prompt_version=prompt.ref)
