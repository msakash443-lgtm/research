"""Concept blocks and synonyms -> a Boolean search string (spec 5.2.1, Appendix A).

A scholar describes a search as *concept blocks*: each block is one idea with its synonyms.
Terms inside a block are OR-ed, blocks are AND-ed:

    ("female labour participation" OR "women's employment")
    AND ("India" OR "South Asia")

This module is pure (no network, no database) and deterministic. It produces the generic
string; per-database syntax (OpenAlex, Semantic Scholar, ...) is the adapters' job (M1.5.2),
and filters such as year or document type are passed beside the query, not inside it.

Terms are validated so that no term can change the structure of the query: quotes,
parentheses, backslashes, control characters and bare operator words (AND/OR/NOT/NEAR) are
refused rather than silently altered, so what the scholar typed is what is searched. Every
term is rendered quoted (a phrase), except a single-word truncation such as `employ*`, which
must stay unquoted because most databases ignore wildcards inside quotes.
"""

from __future__ import annotations

import unicodedata

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_BLOCKS = 10
MAX_TERMS_PER_BLOCK = 50
MAX_TERM_CHARS = 100
MAX_QUERY_CHARS = 2000  # the same ceiling connectors put on a query (SearchRequest.query)
OPERATORS = frozenset({"and", "or", "not", "near", "adj"})
FORBIDDEN_CHARS = frozenset('"()\\')


class QueryError(ValueError):
    """The concept blocks cannot be turned into a usable query."""


def normalise_term(raw: str) -> str:
    """Trim, collapse whitespace and NFC-normalise; raise `ValueError` if the term is unusable."""
    if not isinstance(raw, str):
        raise ValueError("A search term must be text")
    text = unicodedata.normalize("NFC", " ".join(raw.split()))
    if not text:
        raise ValueError("A search term cannot be blank")
    if len(text) > MAX_TERM_CHARS:
        raise ValueError(f"A search term is limited to {MAX_TERM_CHARS} characters")
    for char in text:
        if char in FORBIDDEN_CHARS:
            raise ValueError(f"A search term cannot contain {char!r}; use plain words")
        if unicodedata.category(char).startswith("C"):
            raise ValueError("A search term cannot contain control or invisible characters")
    if text.lower() in OPERATORS:
        raise ValueError(f"{text!r} is a search operator, not a term")
    if "*" in text:
        stem = text[:-1]
        if not text.endswith("*") or "*" in stem or " " in text or len(stem) < 2:
            raise ValueError("A wildcard must be a single word with at least two letters before a trailing *")
    return text


def is_wildcard(term: str) -> bool:
    return term.endswith("*")


class ConceptBlock(BaseModel):
    """One concept and its synonyms. Terms are OR-ed; duplicates (ignoring case) are removed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str | None = Field(default=None, max_length=100)  # for the scholar's own reference only
    terms: tuple[str, ...] = Field(min_length=1, max_length=MAX_TERMS_PER_BLOCK * 2)

    @field_validator("terms")
    @classmethod
    def _terms(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        seen: set[str] = set()
        unique = []
        for raw in value:
            term = normalise_term(raw)
            if term.casefold() not in seen:
                seen.add(term.casefold())
                unique.append(term)
        if len(unique) > MAX_TERMS_PER_BLOCK:
            raise ValueError(f"A concept block is limited to {MAX_TERMS_PER_BLOCK} terms")
        return tuple(unique)

    @field_validator("label")
    @classmethod
    def _label(cls, value: str | None) -> str | None:
        value = " ".join(value.split()) if value else None
        return value or None


class BooleanQuery(BaseModel):
    """Blocks are AND-ed. A query needs at least one block."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    blocks: tuple[ConceptBlock, ...] = Field(min_length=1, max_length=MAX_BLOCKS)

    @model_validator(mode="after")
    def _fits(self) -> "BooleanQuery":
        render(self)  # a query too long for the connectors is refused when it is defined
        return self


def _quote(term: str) -> str:
    return term if is_wildcard(term) else f'"{term}"'


def render_block(block: ConceptBlock) -> str:
    return "(" + " OR ".join(_quote(t) for t in block.terms) + ")"


def render(query: BooleanQuery) -> str:
    """The generic Boolean string, e.g. `("a" OR "b") AND ("c")`. Same input, same output."""
    text = " AND ".join(render_block(b) for b in query.blocks)
    if len(text) > MAX_QUERY_CHARS:
        raise QueryError(f"The query is {len(text)} characters; the limit is {MAX_QUERY_CHARS}. Remove some terms.")
    return text
