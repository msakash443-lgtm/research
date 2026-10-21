"""How well the AI matched this project's own people (X.80; spec 7.7). Read-only: nothing here calls a model or writes.

The hand labels are the project's own human decisions, not a gold set (that is X.5/X.15's job, `app.evaluation`):

* Screening, per stage: the AI's suggestion against the person's own decision (`screening.ai_human_pairs`). A person's
  "maybe" is not a label either way, so it is left out and counted. Recall of includes = of the papers a person
  included, how many the AI also suggested including; `recall_not_excluded` also counts an AI "maybe" (a person still
  sees the paper). Precision of includes = of the papers the AI suggested including, how many a person included.
  A false exclusion is a person's include the AI suggested excluding.
* Extraction: the AI's first values (`Extraction.ai_fields_json`) against the values a person verified
  (`fields_json`), for each paper's newest extraction when it was AI-made and a person verified it. Per field,
  precision and recall use the same rules as `app.evaluation.extraction_eval`. A row made before the AI's first
  values were kept cannot be scored and is counted as such.

What these figures are not: they cover only the papers people have already decided or verified, which is not a random
sample, and they are not a pass/fail test against a gold set. Every denominator that is zero gives `null`, never 0.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Sequence

from sqlalchemy.orm import Session

from app import extraction_verification, screening
from app.evaluation import extraction_eval
from app.extraction_schema import SchemaError
from app.models import Project

CLASSES = ("include", "maybe", "exclude")


def _ratio(part: int, whole: int) -> float | None:
    return round(part / whole, 3) if whole else None


def screening_accuracy(pairs: Iterable[tuple[str, str]]) -> dict[str, Any]:
    """`pairs` are (AI suggestion, person's decision). Returns counts and the include recall/precision."""
    matrix = {human: {ai: 0 for ai in CLASSES} for human in ("include", "exclude")}
    skipped_maybe = 0
    for ai, human in pairs:
        if human == "maybe":
            skipped_maybe += 1
        elif human in matrix and ai in CLASSES:
            matrix[human][ai] += 1
    human_includes = sum(matrix["include"].values())
    ai_includes = matrix["include"]["include"] + matrix["exclude"]["include"]
    return {
        "n": sum(sum(row.values()) for row in matrix.values()), "skipped_human_maybe": skipped_maybe, "matrix": matrix,
        "recall": _ratio(matrix["include"]["include"], human_includes),
        "recall_not_excluded": _ratio(matrix["include"]["include"] + matrix["include"]["maybe"], human_includes),
        "precision": _ratio(matrix["include"]["include"], ai_includes),
        "false_exclusions": matrix["include"]["exclude"],
    }


def extraction_accuracy(items: Sequence[tuple[dict[str, Any], dict[str, Any]]], critical: Iterable[str]) -> dict[str, Any]:
    """`items` are (person's verified values, the AI's first values), one pair per paper."""
    gold = {str(i): human for i, (human, _) in enumerate(items)}
    ai = {str(i): first for i, (_, first) in enumerate(items)}
    result = extraction_eval(gold, ai, critical)
    return {
        "papers": len(items),
        "fields": {
            name: {"tp": s.tp, "fp": s.fp, "fn": s.fn, "precision": _ratio(s.tp, s.tp + s.fp), "recall": _ratio(s.tp, s.tp + s.fn),
                   "critical": name in result.critical}
            for name, s in sorted(result.fields.items())
        },
    }


def project_report(db: Session, project: Project) -> dict[str, Any]:
    stages = {stage: screening_accuracy(screening.ai_human_pairs(db, project, stage)) for stage in ("title_abstract", "full_text")}

    scored: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    critical: dict[str, set[str]] = defaultdict(set)
    ai_made = unverified = no_original = stale_schema = 0
    for _source, row in extraction_verification.current_rows(db, project):
        if row is None or row.extracted_by != "ai":
            continue
        ai_made += 1
        if not row.verified_by_human:
            unverified += 1
        elif row.ai_fields_json is None:
            no_original += 1
        else:
            try:
                schema = extraction_verification.schema_for_row(db, project, row)
            except SchemaError:
                stale_schema += 1
                continue
            scored[row.schema_version].append((dict(row.fields_json or {}), dict(row.ai_fields_json)))
            critical[row.schema_version].update(field.key for field in schema.fields if field.critical)
    return {
        "screening": stages,
        "extraction": {
            "ai_extractions": ai_made, "not_verified_yet": unverified, "no_ai_original_kept": no_original,
            "old_schema": stale_schema,
            "by_schema": {version: extraction_accuracy(items, critical[version]) for version, items in sorted(scored.items())},
        },
        "caveat": "Only papers a person has already decided or verified are counted. That is not a random sample, and this is not a pass/fail test against a gold set.",
    }
