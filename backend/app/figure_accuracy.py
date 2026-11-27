"""Pure checks for the AI accuracy layer of a figure version (plan M8.6).

The model labels each element of one version (each node and each relation) as supported, unsupported or
contradicted by the paper's passages, or abstains. This module builds the element list, the reply schema, and the
check that a reply labels every element exactly once. It does no I/O; `figure_accuracy_task` calls the model.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from app import answer_guard
from app.output_schemas import with_abstention
from app.untrusted_text import clean_untrusted

PROMPT = ("figure_accuracy", 1)
LABELS = ("supported", "unsupported", "contradicted")
CHECKED = "checked"
INSUFFICIENT = "insufficient_evidence"


class AccuracyError(Exception):
    """A reply the check refuses. `code` is the stable failure code the audit records."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


@dataclass(frozen=True)
class Element:
    element_id: str
    description: str


@dataclass(frozen=True)
class Verdict:
    status: str
    labels: dict[str, str]
    reason: str


def _quotes(item: dict) -> str:
    return "; ".join(f'"{evidence["quote"]}"' for evidence in item["evidence"])


def elements(spec: dict) -> list[Element]:
    """One element per node (id: the node id) and per edge (id: `edge:<index>`, so two relations between the same
    pair of nodes stay separate; `:` cannot occur in a node id)."""
    names = {node["id"]: node["label"] for node in spec["nodes"]}
    out = [
        Element(node["id"], f'node "{node["label"]}" (role {node["role"]}); cited: {_quotes(node)}')
        for node in spec["nodes"]
    ]
    for index, edge in enumerate(spec["edges"]):
        relation = f'{names.get(edge["from"], edge["from"])} -> {names.get(edge["to"], edge["to"])}'
        detail = edge["kind"] + (f', "{edge["label"]}"' if edge["label"] else "")
        out.append(Element(f"edge:{index}", f"relation {relation} ({detail}); cited: {_quotes(edge)}"))
    if not out:
        raise AccuracyError("no_elements", "the figure has no elements to check")
    return out


MAX_DESCRIPTION_CHARS = 2_000


def render(items: list[Element]) -> str:
    """Render model-facing element descriptions after cleaning all figure-derived text."""
    return "\n".join(
        f"- {clean_untrusted(item.element_id, 200).text}: "
        f"{clean_untrusted(item.description, MAX_DESCRIPTION_CHARS).text}"
        for item in items
    )


def schema(items: list[Element]) -> dict:
    """The reply schema: one label per element id, or an abstention (`with_abstention` adds the abstention shape)."""
    labelled = {
        "type": "object",
        "additionalProperties": False,
        "required": ["labels"],
        "properties": {
            "labels": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["element_id", "label"],
                    "properties": {
                        "element_id": {"type": "string", "enum": [item.element_id for item in items]},
                        "label": {"type": "string", "enum": list(LABELS)},
                    },
                },
            },
        },
    }
    return with_abstention(labelled, answer_fields=["labels"])


def check_reply(reply: object, items: list[Element], *, system: str, nonce: str) -> Verdict:
    """The verdict for a reply, or `AccuracyError`. A labelled reply must cover every element exactly once and no
    other id; an abstention keeps no labels and carries its reason."""
    if not isinstance(reply, dict):
        raise AccuracyError("invalid_reply", "the reply is not a JSON object")
    if answer_guard.check_answer(json.dumps(reply, sort_keys=True), system_prompt=system, nonce=nonce):
        raise AccuracyError("leaked_prompt", "the reply repeated the instructions")
    abstention = reply.get("insufficient_evidence")
    if isinstance(abstention, dict) and abstention.get("insufficient"):
        return Verdict(INSUFFICIENT, {}, str(abstention.get("reason") or "").strip())
    pairs = reply.get("labels")
    if not isinstance(pairs, list) or not all(isinstance(pair, dict) for pair in pairs):
        raise AccuracyError("incomplete_labels", "the reply has no label list")
    ids = [pair.get("element_id") for pair in pairs]
    if len(set(ids)) != len(ids) or set(ids) != {item.element_id for item in items}:
        raise AccuracyError("incomplete_labels", "the labels do not cover each element exactly once")
    if any(pair.get("label") not in LABELS for pair in pairs):
        raise AccuracyError("incomplete_labels", "a label is not supported, unsupported or contradicted")
    return Verdict(CHECKED, {pair["element_id"]: pair["label"] for pair in pairs}, "")
