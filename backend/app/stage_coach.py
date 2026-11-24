"""Read-only AI explanation of the project's current stage and next actions."""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any

from app.agent.llm import OpenAICompatibleLLM
from app.answer_guard import check_answer
from app.prompt_registry import Prompt, load_prompt

PROMPT = ("stage_coach", 1)
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["explanation", "actions"],
    "properties": {
        "explanation": {"type": "string", "minLength": 1, "maxLength": 2000},
        "actions": {
            "type": "array", "maxItems": 6,
            "items": {"type": "object", "additionalProperties": False,
                      "required": ["text", "condition_id"],
                      "properties": {"text": {"type": "string", "minLength": 1, "maxLength": 500},
                                     "condition_id": {"type": "string", "maxLength": 80}}},
        },
    },
}


class StageCoachError(RuntimeError):
    pass


@dataclass(frozen=True)
class StageCoachResult:
    explanation: str
    actions: list[dict[str, str]]
    model_id: str
    prompt_version: str


def coach(llm: OpenAICompatibleLLM, context: dict[str, Any], *, prompt: Prompt | None = None) -> StageCoachResult:
    prompt = prompt or load_prompt(*PROMPT)
    user = prompt.render_user(context=_context_text(context))
    reply = llm.complete_json(prompt.system, user, SCHEMA)
    explanation = " ".join(reply["explanation"].split())
    actions = [{"text": " ".join(item["text"].split()), "condition_id": item["condition_id"]} for item in reply["actions"]]
    leaked = check_answer(explanation + " " + " ".join(item["text"] for item in actions), system_prompt=prompt.system,
                          nonce=secrets.token_hex(8))
    if leaked:
        raise StageCoachError(f"The stage explanation was rejected because it repeated {leaked}.")
    allowed = {str(item["id"]) for item in context.get("conditions", [])}
    if any(item["condition_id"] not in allowed for item in actions):
        raise StageCoachError("The stage explanation referred to an unknown condition.")
    return StageCoachResult(explanation, actions, llm._model, prompt.ref)


def _context_text(context: dict[str, Any]) -> str:
    lines = [f"Current stage: {context.get('stage', '(unknown)')}",
             f"Research type: {context.get('research_type', '(unknown)')}",
             "Authoritative conditions:"]
    for item in context.get("conditions", []):
        lines.append(f"- {item['id']}: {'met' if item['met'] else 'not met'}; {item['message']}")
    lines.append("Authoritative next stages:")
    for item in context.get("next", []):
        lines.append(f"- {item['stage']} gate={item.get('gate') or 'none'} ready={item['ready']}")
    return "\n".join(lines)
