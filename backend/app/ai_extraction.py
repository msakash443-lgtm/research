"""Retrieval-grounded AI extraction from the exact PDF stored for a source (plan M3.3)."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, project_schemas
from app.answer_guard import check_answer
from app.extraction_schema import ExtractionSchema, SchemaError, ValueSpec, check_extraction, schema_version
from app.extraction_spans import check_evidence_spans
from app.fulltext import FullTextError, extract_pdf_text
from app.models import Extraction, Gate, GateCode, GateStatus, Project, Source
from app.object_storage import ObjectStore, fulltext_key
from app.prompt_registry import Prompt, load_prompt
from app.untrusted_text import clean_untrusted

AI_EXTRACTION_PROMPT = ("evidence_extraction", 1)
AI_EXTRACTOR = "agent:extraction"
MAX_PROMPT_TEXT_CHARS = 80_000
MAX_TITLE_CHARS = 500
MAX_FIELD_DESCRIPTION_CHARS = 1_000
MAX_PAGE_ANCHOR_CHARS = 1_000
DOUBLE_EXTRACTION_NAMESPACE = uuid.UUID("7530e807-6875-4c42-a727-590d2c8c15d9")


class AiExtractionError(RuntimeError):
    """The model's result cannot be stored as an extraction."""


@dataclass(frozen=True)
class AiExtractionResult:
    extraction_id: str
    schema_version: str
    extracted_fields: tuple[str, ...]
    abstained_fields: tuple[str, ...]
    text_flags: tuple[str, ...]


def selected_for_double_extraction(project_id: uuid.UUID, version: str, extraction_id: uuid.UUID) -> bool:
    """Stable one-in-ten bucket per project, schema version, and extraction row."""
    key = f"{project_id}:{version}:{extraction_id}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(key).digest()[:8], "big") % 10 == 0


def compare_extractions(first: dict[str, Any], second: dict[str, Any]) -> dict[str, str]:
    """Field agreement is literal value agreement; abstentions are reported separately."""
    return {
        key: "both_abstained" if first[key] is None and second[key] is None else
             "agree" if first[key] == second[key] else "disagree"
        for key in first
    }


def _value_schema(spec: ValueSpec, *, nullable: bool = False) -> dict[str, Any]:
    if spec.type == "string":
        schema: dict[str, Any] = {"type": "string"}
    elif spec.type == "integer":
        schema = {"type": "integer"}
    elif spec.type == "number":
        schema = {"type": "number"}
    elif spec.type == "list":
        schema = {"type": "array"}
        if spec.items is not None:
            schema["items"] = _value_schema(spec.items)
    else:
        if spec.properties is None:
            schema = {"type": "object"}
        else:
            properties = spec.properties
            schema = {
                "type": "object",
                "properties": {key: _value_schema(child) for key, child in properties.items()},
                "required": sorted(properties),
                "additionalProperties": False,
            }
    if nullable or spec.nullable:
        return {"anyOf": [schema, {"type": "null"}]}
    return schema


def extraction_reply_schema(schema: ExtractionSchema) -> dict[str, Any]:
    """Strict output shape: every schema field has a value/abstention, evidence and confidence."""
    properties: dict[str, Any] = {}
    for field in schema.fields:
        properties[field.key] = {
            "type": "object",
            "additionalProperties": False,
            "required": ["value", "evidence", "confidence"],
            "properties": {
                "value": _value_schema(field, nullable=True),
                "evidence": {
                    "anyOf": [
                        {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["quote", "page", "section"],
                            "properties": {
                                "quote": {"type": "string", "minLength": 1},
                                "page": {"anyOf": [
                                    {"type": "integer", "minimum": 1},
                                    {"type": "string", "minLength": 1},
                                    {"type": "null"},
                                ]},
                                "section": {"anyOf": [
                                    {"type": "string", "minLength": 1},
                                    {"type": "null"},
                                ]},
                            },
                        },
                        {"type": "null"},
                    ]
                },
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
        }
    return {"type": "object", "properties": properties, "required": sorted(properties), "additionalProperties": False}


def _page_text(fulltext, max_chars: int) -> tuple[str, tuple[str, ...]]:
    parts: list[str] = []
    flags: set[str] = set()
    used = 0
    sections_by_page: dict[int, list[str]] = {}
    for section in fulltext.sections:
        cleaned_title = clean_untrusted(section.title, max_chars=MAX_PAGE_ANCHOR_CHARS)
        if cleaned_title.truncated:
            raise AiExtractionError("A stored PDF section heading exceeds the prompt limit; no text was sent to the model.")
        flags.update(cleaned_title.flags)
        sections_by_page.setdefault(section.page, []).append(cleaned_title.text)
    for page in fulltext.pages:
        raw = fulltext.text[page.start:page.end]
        cleaned = clean_untrusted(raw, max_chars=max_chars)
        if cleaned.truncated:
            raise AiExtractionError(f"Stored PDF text exceeds the {max_chars}-character page limit; no text was sent to the model.")
        flags.update(cleaned.flags)
        cleaned_label = clean_untrusted(page.label or "", max_chars=MAX_PAGE_ANCHOR_CHARS)
        if cleaned_label.truncated:
            raise AiExtractionError("A stored PDF page label exceeds the prompt limit; no text was sent to the model.")
        flags.update(cleaned_label.flags)
        printed_label = cleaned_label.text or "not labeled"
        section_titles = "; ".join(sections_by_page.get(page.number, [])) or "unlabeled"
        header = f"[PAGE physical={page.number} printed={printed_label} sections={section_titles}]"
        chunk = f"{header}\n{cleaned.text}"
        used += len(chunk) + 1
        if used > max_chars:
            raise AiExtractionError(f"Stored PDF text exceeds the {max_chars}-character prompt limit; no text was sent to the model.")
        parts.append(chunk)
    return "\n\n".join(parts), tuple(sorted(flags))


def _field_guide(schema: ExtractionSchema) -> str:
    return "\n".join(
        f"- {field.key}: {clean_untrusted(field.description, MAX_FIELD_DESCRIPTION_CHARS).text} "
        f"(type={field.type}; critical={str(field.critical).lower()})"
        for field in schema.fields
    )


def build_extraction_prompts(
    prompt: Prompt, *, title: str, schema: ExtractionSchema, text: str, nonce: str
) -> tuple[str, str, tuple[str, ...]]:
    clean_title = clean_untrusted(title, MAX_TITLE_CHARS)
    clean_document, text_flags = text, ()
    block = prompt.render_source(
        index="1", nonce=nonce, title=clean_title.text, status="stored full-text PDF; unverified source",
        details=f"schema={schema_version(schema)}; fields:\n{_field_guide(schema)}",
        excerpt=clean_document,
    )
    return prompt.system, prompt.render_user(source_block=block), tuple(sorted({*clean_title.flags, *text_flags}))


def extract_with_llm(llm, *, schema: ExtractionSchema, title: str, text: str, prompt: Prompt | None = None) -> tuple[dict, dict, dict, str, tuple[str, ...]]:
    """Return fields, evidence, confidence, model id and cleaning flags or fail with no partial result."""
    prompt = prompt or load_prompt(*AI_EXTRACTION_PROMPT)
    nonce = secrets.token_hex(12)
    system, user, flags = build_extraction_prompts(prompt, title=title, schema=schema, text=text, nonce=nonce)
    reply = llm.complete_json(system, user, extraction_reply_schema(schema), max_tokens=8000)
    leak = check_answer(str(reply), system_prompt=system, nonce=nonce)
    if leak or "<<<" in str(reply) or ">>>" in str(reply):
        raise AiExtractionError(f"The model's extraction was rejected because it repeated {leak or 'a source marker'}.")
    fields: dict[str, Any] = {}
    evidence: dict[str, Any] = {}
    confidence: dict[str, float] = {}
    for spec in schema.fields:
        item = reply[spec.key]
        value, span = item["value"], item["evidence"]
        confidence[spec.key] = float(item["confidence"])
        if value is None:
            if span is not None:
                raise AiExtractionError(f"The model returned evidence for abstained field {spec.key!r}.")
            fields[spec.key] = None
        else:
            if span is None:
                raise AiExtractionError(f"The model returned {spec.key!r} without supporting evidence; the result was rejected.")
            fields[spec.key] = value
            evidence[spec.key] = span
    problems = check_extraction(schema, fields, evidence)
    if problems:
        raise AiExtractionError("The model returned an invalid evidence extraction: " + "; ".join(problems))
    return fields, evidence, confidence, llm._model, flags


def run_ai_extraction(
    db: Session, project: Project, source: Source, *, schema_name: str, task_id, actor: str,
    store: ObjectStore, llm, max_pages: int, ensure_owned: Callable[[Session], None],
) -> AiExtractionResult:
    """Extract one source from its immutable stored PDF and atomically persist an unverified result."""
    existing = db.scalar(select(Extraction).where(Extraction.task_id == task_id))
    if existing is not None:
        return AiExtractionResult(str(existing.id), existing.schema_version, tuple(k for k, v in existing.fields_json.items() if v is not None),
                                  tuple(k for k, v in existing.fields_json.items() if v is None), ())
    if source.merged_into is not None or not source.fulltext_path:
        raise AiExtractionError("This source has no stored full-text PDF available for extraction.")
    if source.fulltext_path != fulltext_key(project.id, source.id, "pdf"):
        raise AiExtractionError("The stored full-text reference does not identify this source's PDF.")
    if db.scalar(select(Gate.status).where(Gate.project_id == project.id, Gate.code == GateCode.G4)) == GateStatus.approved:
        raise AiExtractionError("Gate G4 is approved; reopen the extraction stage before creating another extraction.")
    try:
        schema = project_schemas.resolve(db, project.id, schema_name)
        version = schema_version(schema)
        extraction_id = uuid.uuid5(DOUBLE_EXTRACTION_NAMESPACE, str(task_id))
        pdf_bytes = store.get(source.fulltext_path)
        fulltext = extract_pdf_text(pdf_bytes, max_pages=max_pages)
        text, flags = _page_text(fulltext, MAX_PROMPT_TEXT_CHARS)
        fields, evidence, confidence, model_id, prompt_flags = extract_with_llm(
            llm, schema=schema, title=source.title, text=text
        )
        span_problems = check_evidence_spans(fulltext, fields, evidence)
        if span_problems:
            raise AiExtractionError(
                "The model returned evidence that does not match the stored PDF: "
                + "; ".join(span_problems)
            )
        agreement = None
        if selected_for_double_extraction(project.id, version, extraction_id):
            second_fields, second_evidence, second_confidence, second_model_id, second_flags = extract_with_llm(
                llm, schema=schema, title=source.title, text=text
            )
            second_problems = check_evidence_spans(fulltext, second_fields, second_evidence)
            if second_problems:
                raise AiExtractionError(
                    "The second extraction contains evidence that does not match the stored PDF: "
                    + "; ".join(second_problems)
                )
            agreement = {
                "fields": compare_extractions(fields, second_fields),
                "second_fields": second_fields,
                "second_evidence": second_evidence,
                "second_confidence": second_confidence,
                "second_model_id": second_model_id,
                "second_prompt_version": AI_EXTRACTION_PROMPT[0] + "@" + str(AI_EXTRACTION_PROMPT[1]),
            }
            flags = tuple(sorted({*flags, *second_flags}))
    except (SchemaError, FullTextError) as exc:
        raise AiExtractionError(str(exc)) from exc
    flags = tuple(sorted({*flags, *prompt_flags}))
    gate = db.scalar(select(Gate).where(Gate.project_id == project.id, Gate.code == GateCode.G4).with_for_update())
    if gate is not None and gate.status == GateStatus.approved:
        raise AiExtractionError("Gate G4 was approved while extraction was running; no result was saved.")
    ensure_owned(db)
    row = Extraction(
        id=extraction_id,
        task_id=task_id, project_id=project.id, source_id=source.id, schema_version=schema_version(schema),
        fields_json=fields, evidence_spans=evidence, confidence_json=confidence, agreement_json=agreement, verified_by_human=False,
        extracted_by="ai", extractor=AI_EXTRACTOR, model_id=model_id, prompt_version=AI_EXTRACTION_PROMPT[0] + "@" + str(AI_EXTRACTION_PROMPT[1]),
    )
    db.add(row)
    db.flush()
    audit.record(
        db, actor=AI_EXTRACTOR, action="extraction.ai_created", project_id=project.id,
        payload={"task_id": str(task_id), "extraction_id": str(row.id), "source_id": str(source.id),
                 "schema_version": row.schema_version, "fields": sorted(k for k, v in fields.items() if v is not None),
                 "abstained_fields": sorted(k for k, v in fields.items() if v is None), "text_flags": list(flags),
                 "requested_by": actor, "double_extracted": agreement is not None,
                 "disagreements": sum(result == "disagree" for result in agreement["fields"].values()) if agreement else 0},
        model_id=model_id, prompt_version=row.prompt_version,
    )
    ensure_owned(db)
    db.commit()
    return AiExtractionResult(str(row.id), row.schema_version, tuple(k for k, v in fields.items() if v is not None),
                              tuple(k for k, v in fields.items() if v is None), flags)
