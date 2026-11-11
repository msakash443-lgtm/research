"""One-action audit/replication package (spec 13.6 #6, plan X.8.1).

A zip of everything the project stored, so someone else can see what was searched, screened, extracted
and generated, by whom, with which model and prompt version, and check the files weren't changed:

* `data/<table>.json` and `data/<table>.csv` for every project-scoped table, rows in a stable order;
* `derived/prisma.json`: the PRISMA counts computed at export time;
* `derived/agreement.json`: Cohen's kappa per screening stage, its scope counts and the conflict list, computed at export time;
* `derived/known_items.json`, `derived/recall_report.json`: seed-paper coverage and gold-set recall (X.8.4); `derived/references.bib`: BibTeX of verified sources;
* `manifest.json`: sha256 and row count per file, schema revision, prompt/schema/model versions seen,
  what was left out and why, who exported and when;
* `README.md`: how to read it.

Tables are found from the ORM metadata, so a new project table is exported without changing this file.
Left out on purpose (listed in the manifest): per-user notes, the task queue, embedding vectors, user
accounts and emails. Full-text PDFs are never included, only their stored paths.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import uuid
import zipfile
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy import Table, select
from sqlalchemy.orm import Session

from app import known_items, prisma, recall_report
from app.database import Base
from app.models import Project, Source
from app.refmanager import source_records, to_bibtex
from app.screening import STAGES, stage_agreement

FORMAT_VERSION = 1
# Spreadsheet apps treat cells starting with these as formulas (same guard as the audit CSV export).
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

# Tables with a project_id that are deliberately not exported, and why (written into the manifest).
EXCLUDED_TABLES = {
    "notes": "per-user notes and inbox (private to their author)",
    "tasks": "operational job queue; what ran is in the audit log and the run records",
}
# Columns dropped from exported tables, and why.
EXCLUDED_COLUMNS = {
    "source_embeddings": {"vector": "embedding vectors are derived from the sources and large; the model and text hash are kept"},
}
# Tables without a project_id that belong to the project through a parent row: (table, parent table, fk column).
CHILD_TABLES = [
    ("source_excerpts", "sources", "source_id"),
    ("claim_evidence", "claims", "claim_id"),
    ("clusters", "cluster_runs", "run_id"),
    ("cluster_members", "clusters", "cluster_id"),
    ("ai_suggestion_events", "ai_suggestions", "suggestion_id"),
]
NOT_EXPORTED = {
    "users": "accounts and emails are personal data; members are exported as user id + role",
    "external_identities": "sign-in identities (personal data)",
    "note_revisions": "history of per-user notes (see notes)",
    "note_attachments": "binary attachments of per-user notes (private object storage)",
    "connector_breaker_state": "operational circuit-breaker state (M1.3.8), not scoped to any project",
}


def _plain(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return None  # no binary columns exist today; never export raw bytes
    return value


def _csv_cell(value: Any) -> str:
    if value is None:
        return ""
    cell = json.dumps(value, sort_keys=True, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
    return "'" + cell if cell.startswith(_FORMULA_PREFIXES) else cell


def _order(table: Table) -> list:
    keys = [table.c[name] for name in ("created_at", "timestamp", "position", "seq") if name in table.c]
    return keys + list(table.primary_key.columns)


def _rows(db: Session, table: Table, where) -> tuple[list[str], list[dict[str, Any]]]:
    dropped = EXCLUDED_COLUMNS.get(table.name, {})
    columns = [c for c in table.columns if c.name not in dropped]
    result = db.execute(select(*columns).where(where).order_by(*_order(table)))
    names = [c.name for c in columns]
    return names, [{name: _plain(value) for name, value in zip(names, row)} for row in result]


def _csv(names: list[str], rows: list[dict[str, Any]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(names)
    for row in rows:
        writer.writerow([_csv_cell(row[name]) for name in names])
    return buffer.getvalue().encode("utf-8")


def _json(value: Any) -> bytes:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, default=_plain).encode("utf-8")


def project_tables() -> list[Table]:
    """Every mapped table with a `project_id` column, minus the excluded ones, by name."""
    return sorted(
        (t for t in Base.metadata.tables.values() if "project_id" in t.c and t.name not in EXCLUDED_TABLES),
        key=lambda t: t.name,
    )


def schema_revision() -> str | None:
    """The newest Alembic revision in the code (what this export's columns correspond to)."""
    try:
        from pathlib import Path

        from alembic.config import Config
        from alembic.script import ScriptDirectory

        config = Config()
        config.set_main_option("script_location", str(Path(__file__).resolve().parent.parent / "migrations"))
        heads = ScriptDirectory.from_config(config).get_heads()
        return ",".join(sorted(heads)) or None
    except Exception:  # the package is still useful without it; the manifest says it is unknown
        return None


README = """# Replication package

Exported from the research workspace. Everything here is the project's stored record; nothing was
regenerated for the export.

* `manifest.json` lists every file with its sha256 and row count. Check a file with
  `sha256sum data/sources.json` (or `Get-FileHash -Algorithm SHA256` on Windows).
* `data/<table>.json` and `.csv` hold the same rows. In the CSV, JSON values are written as JSON text and
  a cell that would start a spreadsheet formula is prefixed with `'`.
* `data/audit_events` is the append-only trail of who did what (user ids or `agent:<name>`), with the
  model and prompt version of every AI step.
* `data/research_runs` keeps each run's `input_snapshot`: the exact sources and excerpts the model saw.
* `data/search_queries` is the versioned search log; `derived/prisma.json` the PRISMA counts at export time; `derived/agreement.json` the Cohen's kappa, scope counts and conflicts per stage at export time.
* `derived/known_items.json` says which seed papers the searches found and which they missed;
  `derived/recall_report.json` is the gold-set recall against the 95% target with the reason for each miss;
  `derived/references.bib` is BibTeX for the metadata-verified sources only (unverified ones are left out).
* AI pre-screen suggestions are rows in `data/screening_decisions` with `decided_by = "ai"`; they are not
  decisions.
* A row with `decided_by = "arbiter"` is an owner's or supervisor's ruling on two reviewers who disagreed. It settles
  that record's outcome; the two reviewers' own rows stay, and `derived/agreement.json` still compares only them.

Left out on purpose (see `manifest.json` → `excluded`): per-user notes, the task queue, embedding vectors,
user accounts/emails and all full-text PDFs (only their stored paths). The package can contain quoted text
and abstracts from papers: check licences before sharing it outside the project team.
"""


def build_package(db: Session, project: Project, *, exported_by: str, exported_at: datetime) -> tuple[bytes, dict[str, Any]]:
    """The zip bytes and a summary (file count, row counts) for the audit event."""
    files: dict[str, bytes] = {}
    counts: dict[str, int] = {}
    ids_by_table: dict[str, list[Any]] = {}

    def add_table(table: Table, where) -> None:
        names, rows = _rows(db, table, where)
        files[f"data/{table.name}.json"] = _json(rows)
        files[f"data/{table.name}.csv"] = _csv(names, rows)
        counts[table.name] = len(rows)
        if "id" in table.c:
            ids_by_table[table.name] = [uuid.UUID(r["id"]) if isinstance(r["id"], str) else r["id"] for r in rows]

    projects = Base.metadata.tables["projects"]
    add_table(projects, projects.c.id == project.id)
    for table in project_tables():
        add_table(table, table.c.project_id == project.id)
    for name, parent, fk in CHILD_TABLES:
        table = Base.metadata.tables[name]
        add_table(table, table.c[fk].in_(ids_by_table.get(parent, [])))

    files["derived/prisma.json"] = _json(prisma.flow_counts(db, project))
    agreement_json = {}
    for stage in STAGES:
        result = stage_agreement(db, project, stage)
        a = result.agreement
        agreement_json[stage] = {
            "n": a.n, "agreed": a.agreed, "percent_agreement": a.percent_agreement, "kappa": a.kappa, "band": a.band,
            "categories": list(a.categories), "matrix": a.matrix, "note": a.note,
            "scope": result.scope, "conflicts": result.conflicts,
        }
    files["derived/agreement.json"] = _json(agreement_json)
    files["derived/known_items.json"] = _json(known_items.check(db, project))
    files["derived/recall_report.json"] = _json(recall_report.project_report(db, project))
    verified = db.scalars(
        select(Source).where(Source.project_id == project.id, Source.merged_into.is_(None), Source.metadata_verified.is_(True))
        .order_by(Source.created_at, Source.id)
    ).all()
    files["derived/references.bib"] = to_bibtex(source_records(verified)).encode("utf-8")

    rows_of = lambda name: json.loads(files[f"data/{name}.json"])  # noqa: E731
    versions = {
        "prompt_versions": sorted({r["prompt_version"] for t in ("research_runs", "extractions", "audit_events") for r in rows_of(t) if r.get("prompt_version")}),
        "model_ids": sorted(
            {r[k] for t, k in (("research_runs", "provider_model"), ("extractions", "model_id"), ("audit_events", "model_id"), ("cluster_runs", "model_id"))
             for r in rows_of(t) if r.get(k)}
        ),
        "extraction_schema_versions": sorted({r["schema_version"] for r in rows_of("extractions")}),
    }
    files["README.md"] = README.encode("utf-8")
    manifest = {
        "format_version": FORMAT_VERSION,
        "project_id": str(project.id),
        "project_title": project.title,
        "exported_at": exported_at.isoformat(),
        "exported_by": exported_by,
        "schema_revision": schema_revision(),
        **versions,
        "row_counts": dict(sorted(counts.items())),
        "files": {name: {"sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)} for name, body in sorted(files.items())},
        "excluded": {
            "tables": {**EXCLUDED_TABLES, **NOT_EXPORTED},
            "columns": {t: cols for t, cols in EXCLUDED_COLUMNS.items()},
            "files": "full-text PDFs and other stored objects are never included (only `sources.fulltext_path`)",
        },
    }
    buffer = io.BytesIO()
    fixed_time = (1980, 1, 1, 0, 0, 0)  # constant zip timestamps: the same data gives the same entries
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, body in sorted({**files, "manifest.json": _json(manifest)}.items()):
            archive.writestr(zipfile.ZipInfo(name, date_time=fixed_time), body, compress_type=zipfile.ZIP_DEFLATED)
    summary = {"files": len(files) + 1, "row_counts": dict(sorted(counts.items())), "schema_revision": manifest["schema_revision"]}
    return buffer.getvalue(), summary


__all__ = ["EXCLUDED_TABLES", "FORMAT_VERSION", "build_package", "project_tables", "schema_revision"]
