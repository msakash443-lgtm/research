"""Validation and canonicalisation for M6.10 generated front matter.

This module contains no database, HTTP or model calls. Generated content is a proposal until a
researcher accepts it; callers must bind the result to a specific DocumentVersion hash.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

OutputKind = Literal["abstract", "keywords", "highlights", "cover_letter"]
OUTPUT_KINDS = frozenset(("abstract", "keywords", "highlights", "cover_letter"))


class FrontMatterError(ValueError):
    pass


@dataclass(frozen=True)
class FrontMatter:
    kind: OutputKind
    text: str
    source_version_id: str
    source_hash: str


def canonical_text(kind: str, value: object) -> str:
    if kind not in OUTPUT_KINDS:
        raise FrontMatterError("unknown front-matter output kind")
    if not isinstance(value, str) or not value.strip():
        raise FrontMatterError("generated output must be non-empty text")
    text = value.strip()
    if len(text) > 20_000:
        raise FrontMatterError("generated output exceeds the 20,000-character limit")
    if kind == "keywords":
        words = [part.strip() for part in text.replace(";", ",").split(",")]
        words = [word for word in words if word]
        if not words or len(words) > 30 or len({word.casefold() for word in words}) != len(words):
            raise FrontMatterError("keywords must be 1–30 unique comma-separated terms")
        text = ", ".join(words)
    return text


def source_hash(content_json: object) -> str:
    canonical = json.dumps(content_json, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build(kind: str, value: object, *, source_version_id: str, source_content_json: object) -> FrontMatter:
    if not isinstance(source_version_id, str) or not source_version_id:
        raise FrontMatterError("source_version_id is required")
    text = canonical_text(kind, value)
    return FrontMatter(kind=kind, text=text, source_version_id=source_version_id,
                       source_hash=source_hash(source_content_json))
