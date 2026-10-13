"""Read-only papers by schema fields view of the project's current extractions."""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from xml.sax.saxutils import escape

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.extraction_schema import schema_version
from app.models import Extraction, Project
from app.project_schemas import resolve
from app.thematic_clusters import clusterable_sources


def build(db: Session, project: Project, name: str = "default") -> dict:
    schema = resolve(db, project.id, name)
    version = schema_version(schema)
    sources = {source.id: source for source in clusterable_sources(db, project)}
    chosen = {}
    for row in db.scalars(select(Extraction).where(
        Extraction.project_id == project.id, Extraction.schema_version == version
    )):
        if row.source_id not in sources:
            continue
        previous = chosen.get(row.source_id)
        rank = lambda item: (item.verified_by_human, item.updated_at or item.created_at, str(item.id))
        if previous is None or rank(row) > rank(previous):
            chosen[row.source_id] = row
    papers = []
    for source_id, source in sources.items():
        row = chosen.get(source_id)
        cells = {}
        for field in schema.fields:
            value = (row.fields_json or {}).get(field.key) if row else None
            span = (row.evidence_spans or {}).get(field.key) or {} if row else {}
            cells[field.key] = {
                "value": value, "status": "not_extracted" if row is None else "absent" if value is None else (
                    "checked" if row.verified_by_human else "matched"
                ),
                "quote": span.get("quote"), "page": span.get("page"), "section": span.get("section"),
                "source_text_url": (
                    f"/api/projects/{project.id}/evidence-matrix/{row.id}/fields/{field.key}/source-text"
                    if row and value is not None else None
                ),
            }
        papers.append({
            "source_id": str(source_id), "title": source.title,
            "extraction_id": str(row.id) if row else None,
            "verified_by_human": row.verified_by_human if row else False,
            "extracted_by": row.extracted_by if row else None,
            "model_id": row.model_id if row else None,
            "prompt_version": row.prompt_version if row else None,
            "created_at": row.created_at.isoformat() if row else None,
            "cells": cells,
        })
    papers.sort(key=lambda paper: (paper["title"].casefold(), paper["source_id"]))
    return {
        "schema_version": version, "schema_label": schema.label,
        "fields": [{"key": field.key, "description": field.description, "critical": field.critical}
                   for field in schema.fields],
        "papers": papers,
    }


def _safe(value) -> str:
    text = "" if value is None else (json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value))
    return "'" + text if re.match(r"^\s*[=+@\-\t\r]", text) else text


def tabular(matrix: dict) -> tuple[list[str], list[list[str]]]:
    headers = ["source_id", "title", "extraction_id", "schema_version", "verified_by_human"]
    for field in matrix["fields"]:
        headers.extend(f"{field['key']}_{part}" for part in ("value", "status", "quote", "page", "section"))
    rows = []
    for paper in matrix["papers"]:
        row = [paper["source_id"], paper["title"], paper["extraction_id"],
               matrix["schema_version"], str(paper["verified_by_human"]).lower()]
        for field in matrix["fields"]:
            cell = paper["cells"][field["key"]]
            row.extend(_safe(cell[part]) for part in ("value", "status", "quote", "page", "section"))
        rows.append([_safe(value) for value in row])
    return headers, rows


def csv_bytes(matrix: dict) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    headers, rows = tabular(matrix)
    writer.writerow(headers)
    writer.writerows(rows)
    return ("\ufeff" + output.getvalue()).encode("utf-8")


def xlsx_bytes(matrix: dict) -> bytes:
    """Small XLSX with inline strings; all user data remains text, never formulas."""
    headers, rows = tabular(matrix)
    def cell(value: str) -> str:
        valid = "".join(char for char in value if char in "\t\n\r" or 0x20 <= ord(char) <= 0xD7FF
                        or 0xE000 <= ord(char) <= 0xFFFD or 0x10000 <= ord(char) <= 0x10FFFF)
        return f'<c t="inlineStr"><is><t xml:space="preserve">{escape(valid)}</t></is></c>'
    sheet = '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
    for index, row in enumerate([headers, *rows], 1):
        sheet += f'<row r="{index}">' + "".join(cell(value) for value in row) + '</row>'
    sheet += '</sheetData></worksheet>'
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        archive.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Evidence" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr('xl/worksheets/sheet1.xml', sheet)
    return output.getvalue()
