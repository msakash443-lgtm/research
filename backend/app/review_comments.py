"""Pure validation for structured M7.1 review comments."""

from __future__ import annotations

from typing import Literal

CommentSeverity = Literal["info", "minor", "major", "blocking"]
CommentStatus = Literal["open", "accepted", "resolved", "rejected"]


def validate_comment(*, severity: str, status: str, body: str) -> tuple[str, str, str]:
    if severity not in {"info", "minor", "major", "blocking"}:
        raise ValueError("unknown review comment severity")
    if status not in {"open", "accepted", "resolved", "rejected"}:
        raise ValueError("unknown review comment status")
    if not isinstance(body, str) or not body.strip() or len(body) > 4000:
        raise ValueError("review comment body must be 1–4000 characters")
    return severity, status, body.strip()
