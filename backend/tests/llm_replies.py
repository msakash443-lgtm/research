"""Builders for fake model replies in the structured shape research runs now expect (M0.8.6)."""
import json


def structured(answer, confidence=0.8):
    return {"answer": answer, "confidence": confidence, "insufficient_evidence": {"insufficient": False, "reason": ""}}


def abstention(reason="No source covers this."):
    return {"answer": "", "confidence": 0.0, "insufficient_evidence": {"insufficient": True, "reason": reason}}


def chat_reply(answer, confidence=0.8):
    """An OpenAI-style chat completion whose content is the structured JSON for `answer`."""
    return {"choices": [{"message": {"content": json.dumps(structured(answer, confidence))}}]}
