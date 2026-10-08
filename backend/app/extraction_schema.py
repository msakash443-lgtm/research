"""Extraction schema registry (plan M3.1, spec 5.4.1, Appendix B): what to pull out of a paper, as validated config.

A schema is a JSON file in `app/extraction_schemas/`. Every field is either null (absent from the paper) or
carries a value plus `evidence {quote, page, section}`. `check_extraction` enforces that rule on a payload;
whether the quote really appears in the paper is a separate check (M3.4).
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_DIR = Path(__file__).resolve().parent / "extraction_schemas"
_ID = re.compile(r"^[a-z][a-z0-9_]{1,59}$")
_TYPES = {"string": str, "integer": int, "number": (int, float), "list": list, "object": dict}


class SchemaError(RuntimeError):
    pass


class FieldSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(pattern=_ID.pattern)
    type: Literal["string", "integer", "number", "list", "object"]
    description: str = Field(min_length=1, max_length=500)
    critical: bool = False  # critical fields need a person's check at gate G4 (M3.6)


class ExtractionSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    version: int = Field(ge=1)
    label: str
    description: str
    fields: list[FieldSpec] = Field(min_length=1)

    def field(self, key: str) -> FieldSpec | None:
        return next((f for f in self.fields if f.key == key), None)


@lru_cache(maxsize=None)
def _load(directory: str, name: str) -> ExtractionSchema:
    path = Path(directory) / f"{name}.json"
    if not _ID.match(name) or not path.is_file():
        raise SchemaError(f"Extraction schema {name!r} does not exist")
    try:
        schema = ExtractionSchema(**json.loads(path.read_text(encoding="utf-8")))
    except Exception as exc:  # JSON syntax, missing keys, bad field
        raise SchemaError(f"Extraction schema {path.name} is invalid: {exc}") from exc
    keys = [f.key for f in schema.fields]
    if len(set(keys)) != len(keys):
        raise SchemaError(f"Extraction schema {path.name} repeats a field key")
    if schema.name != name:
        raise SchemaError(f"Extraction schema {path.name} declares name {schema.name!r}")
    return schema


def load_schema(name: str = "default") -> ExtractionSchema:
    return _load(str(SCHEMA_DIR), name)


def available_schemas() -> list[str]:
    return sorted(path.stem for path in SCHEMA_DIR.glob("*.json"))


def schema_version(schema: ExtractionSchema) -> str:
    """The string stored on an extraction, so it records exactly which schema produced it."""
    return f"{schema.name}@{schema.version}"


def check_extraction(schema: ExtractionSchema, fields: dict[str, Any], evidence: dict[str, Any]) -> list[str]:
    """Problems with an extraction payload; an empty list means it is well formed.

    `fields` maps field key -> value (None = not in the paper); `evidence` maps field key -> {quote, page, section}.
    """
    problems: list[str] = []
    for key in sorted((set(fields) | set(evidence)) - {f.key for f in schema.fields}):
        problems.append(f"{key}: not a field of schema {schema_version(schema)}")
    for spec in schema.fields:
        value = fields.get(spec.key)
        span = evidence.get(spec.key)
        if value is None:
            if span is not None:
                problems.append(f"{spec.key}: evidence given for an empty field")
            continue
        expected = _TYPES[spec.type]
        if isinstance(value, bool) or not isinstance(value, expected):
            problems.append(f"{spec.key}: expected {spec.type}")
        if not isinstance(span, dict):
            problems.append(f"{spec.key}: a non-null field needs evidence {{quote, page, section}}")
            continue
        quote = span.get("quote")
        if not isinstance(quote, str) or not quote.strip():
            problems.append(f"{spec.key}: evidence needs a non-empty quote")
        if span.get("page") is None and not (isinstance(span.get("section"), str) and span["section"].strip()):
            problems.append(f"{spec.key}: evidence needs a page or a section")
    return problems
