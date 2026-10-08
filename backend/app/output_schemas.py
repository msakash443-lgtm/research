"""Shared confidence + abstention shape for structured LLM output (M0.8.3). Pure data, no DB or network.

A reply either answers (with a confidence) or says "insufficient evidence" with a reason; it can't
do both, and an abstention can't be empty. Pass the schema to `OpenAICompatibleLLM.complete_json`,
so a reply that breaks these rules is rejected and retried, then fails the step (no silent default).
"""
from __future__ import annotations

import copy

CONFIDENCE_FIELD = {"type": "number", "minimum": 0, "maximum": 1}

# What every abstaining output carries. `reason` must say what is missing, so it can be shown to the scholar.
INSUFFICIENT_EVIDENCE_FIELD = {
    "type": "object",
    "additionalProperties": False,
    "required": ["insufficient", "reason"],
    "properties": {
        "insufficient": {"type": "boolean"},
        "reason": {"type": "string", "maxLength": 1000},
    },
}


def with_abstention(schema: dict, *, answer_fields: list[str]) -> dict:
    """Return a copy of an object `schema` that also requires `confidence` and `insufficient_evidence`.

    `answer_fields` are the properties that carry the actual answer. They must be non-empty when the
    model answers and are not allowed to hold content when it abstains.
    """
    if schema.get("type") != "object" or not answer_fields:
        raise ValueError("with_abstention needs an object schema and at least one answer field")
    properties = schema.get("properties", {})
    missing = [name for name in answer_fields if name not in properties]
    if missing:
        raise ValueError(f"answer fields not in the schema: {missing}")
    out = copy.deepcopy(schema)
    out["properties"]["confidence"] = dict(CONFIDENCE_FIELD)
    out["properties"]["insufficient_evidence"] = copy.deepcopy(INSUFFICIENT_EVIDENCE_FIELD)
    out["required"] = sorted(set(out.get("required", [])) | {"confidence", "insufficient_evidence"})
    out["additionalProperties"] = False
    out["allOf"] = [
        {
            "if": {"properties": {"insufficient_evidence": {"properties": {"insufficient": {"const": True}}}}},
            "then": {
                "properties": {
                    "insufficient_evidence": {"properties": {"reason": {"minLength": 1, "pattern": r"\S"}}},
                    **{name: {"type": ["string", "null", "array"], "maxLength": 0, "maxItems": 0} for name in answer_fields},
                }
            },
            "else": {
                "properties": {
                    "insufficient_evidence": {"properties": {"reason": {"maxLength": 0}}},
                    # A whitespace-only string is not an answer (M0.8.7); `pattern` only applies to strings.
                    **{name: {"not": {"type": "null"}, "minLength": 1, "minItems": 1, "pattern": r"\S"} for name in answer_fields},
                }
            },
        }
    ]
    return out


# The live research-run answer (switched over in M0.8.6): prose answer + confidence + abstention.
EVIDENCE_SYNTHESIS_SCHEMA = with_abstention(
    {
        "type": "object",
        "required": ["answer"],
        "properties": {"answer": {"type": ["string", "null"], "maxLength": 20000}},
    },
    answer_fields=["answer"],
)
