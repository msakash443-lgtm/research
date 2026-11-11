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

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_DIR = Path(__file__).resolve().parent / "extraction_schemas"
_ID = re.compile(r"^[a-z][a-z0-9_]{1,59}$")
_TYPES = {"string": str, "integer": int, "number": (int, float), "list": list, "object": dict}


class SchemaError(RuntimeError):
    pass


class ValueSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["string", "integer", "number", "list", "object"]
    nullable: bool = False
    items: ValueSpec | None = None
    properties: dict[str, ValueSpec] | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> ValueSpec:
        if self.items is not None and self.type != "list":
            raise ValueError("items is only valid for list values")
        if self.properties is not None and self.type != "object":
            raise ValueError("properties is only valid for object values")
        return self


class FieldSpec(ValueSpec):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=_ID.pattern)
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


def _check_value(spec: ValueSpec, value: Any, path: str, problems: list[str]) -> None:
    if value is None and spec.nullable:
        return
    expected = _TYPES[spec.type]
    if isinstance(value, bool) or not isinstance(value, expected):
        problems.append(f"{path}: expected {spec.type}")
        return
    if spec.items is not None:
        for index, item in enumerate(value):
            _check_value(spec.items, item, f"{path}[{index}]", problems)
    if spec.properties is not None:
        for key in sorted(value.keys() - spec.properties.keys()):
            problems.append(f"{path}.{key}: unexpected property")
        for key in sorted(spec.properties.keys() - value.keys()):
            problems.append(f"{path}.{key}: missing property")
        for key in sorted(spec.properties.keys() & value.keys()):
            _check_value(spec.properties[key], value[key], f"{path}.{key}", problems)


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
        _check_value(spec, value, spec.key, problems)
        if not isinstance(span, dict):
            problems.append(f"{spec.key}: a non-null field needs evidence {{quote, page, section}}")
            continue
        quote = span.get("quote")
        if not isinstance(quote, str) or not quote.strip():
            problems.append(f"{spec.key}: evidence needs a non-empty quote")
        page = span.get("page")
        valid_page = (
            isinstance(page, int) and not isinstance(page, bool) and page > 0
        ) or (isinstance(page, str) and bool(page.strip()))
        if page is not None and not valid_page:
            problems.append(f"{spec.key}: page must be a positive number or a non-empty printed label")
        if span.get("page") is None and not (isinstance(span.get("section"), str) and span["section"].strip()):
            problems.append(f"{spec.key}: evidence needs a page or a section")
    return problems
