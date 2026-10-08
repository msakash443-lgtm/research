"""Method × population × country matrices from extractions (spec 5.5.4, plan M3.8.2).

Counts which combinations the project's extracted papers cover, so a person can see the cells nobody
studied. Pure counting, no AI. The dimensions come from the default extraction schema:

* `method`     -> `fields.method.design`
* `population` -> `fields.sample.population`
* `country`    -> `fields.sample.country`

Rules, stated plainly:

* one extraction per paper: the one a person verified if there is one, else the most recently updated;
* papers merged into another or excluded by a person at screening are left out;
* values are grouped ignoring case and repeated spaces; a group is shown with its most common spelling;
* a paper missing a value for an axis in use is counted under `not_reported`, never put in a cell;
* an empty cell means no extracted paper *in this project* has that combination. It is a prompt to look,
  not evidence that no research exists (spec 5.5.5 still needs supporting papers for a gap).
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Extraction, Project
from app.thematic_clusters import clusterable_sources  # same "active, not excluded by a person" rule

DIMENSIONS: dict[str, tuple[str, str]] = {
    "method": ("method", "design"),
    "population": ("sample", "population"),
    "country": ("sample", "country"),
}
MAX_VALUE_CHARS = 200


@dataclass(frozen=True)
class Paper:
    source_id: uuid.UUID
    title: str
    extraction_id: uuid.UUID
    verified: bool
    values: dict[str, str | None]  # dimension -> raw value (None = not reported)


def _key(value: str) -> str:
    return " ".join(value.split()).casefold()


def dimension_value(fields: dict[str, Any], dimension: str) -> str | None:
    """The raw value for a dimension, or None when it isn't a non-blank string."""
    outer, inner = DIMENSIONS[dimension]
    block = fields.get(outer) if isinstance(fields, dict) else None
    value = block.get(inner) if isinstance(block, dict) else None
    if not isinstance(value, str) or not value.strip():
        return None
    return " ".join(value.split())[:MAX_VALUE_CHARS]


def _rank(row: Extraction) -> tuple:
    """Verified by a person first, then the most recently updated (id breaks ties)."""
    return (row.verified_by_human, row.updated_at or row.created_at, str(row.id))


def papers_for_project(db: Session, project: Project, *, verified_only: bool = False) -> list[Paper]:
    """One `Paper` per active source that has an extraction (see the module rules)."""
    sources = {s.id: s for s in clusterable_sources(db, project)}
    chosen: dict[uuid.UUID, Extraction] = {}
    for row in db.scalars(select(Extraction).where(Extraction.project_id == project.id)):
        if row.source_id not in sources or (verified_only and not row.verified_by_human):
            continue
        current = chosen.get(row.source_id)
        if current is None or _rank(row) > _rank(current):
            chosen[row.source_id] = row
    papers = [
        Paper(
            source_id=sid, title=sources[sid].title, extraction_id=row.id, verified=row.verified_by_human,
            values={d: dimension_value(row.fields_json, d) for d in DIMENSIONS},
        )
        for sid, row in chosen.items()
    ]
    return sorted(papers, key=lambda p: (p.title.casefold(), str(p.source_id)))


@dataclass
class Axis:
    labels: dict[str, str] = field(default_factory=dict)  # key -> display label

    @classmethod
    def from_values(cls, values: list[str]) -> "Axis":
        spellings: dict[str, Counter] = {}
        for value in values:
            spellings.setdefault(_key(value), Counter())[value] += 1
        labels = {k: min(c, key=lambda s: (-c[s], s)) for k, c in spellings.items()}
        return cls(dict(sorted(labels.items(), key=lambda item: item[1].casefold())))


def build_matrix(papers: list[Paper], rows: str, cols: str, layer: str | None = None) -> dict[str, Any]:
    """The matrix (one per `layer` value when a layer is given) as plain JSON-ready data."""
    used = [d for d in (rows, cols, layer) if d]
    if len(set(used)) != len(used) or any(d not in DIMENSIONS for d in used):
        raise ValueError(f"rows, cols and layer must be different dimensions from: {', '.join(DIMENSIONS)}")
    placed = [p for p in papers if all(p.values[d] is not None for d in used)]
    not_reported = {d: sum(1 for p in papers if p.values[d] is None) for d in used}
    row_axis = Axis.from_values([p.values[rows] for p in placed])  # type: ignore[misc]
    col_axis = Axis.from_values([p.values[cols] for p in placed])  # type: ignore[misc]
    layer_axis = Axis.from_values([p.values[layer] for p in placed]) if layer else None  # type: ignore[misc]

    def one(layer_key: str | None) -> dict[str, Any]:
        members = [p for p in placed if layer_key is None or _key(p.values[layer]) == layer_key]  # type: ignore[arg-type]
        cells, empty = [], []
        for rk, rlabel in row_axis.labels.items():
            for ck, clabel in col_axis.labels.items():
                inside = [p for p in members if _key(p.values[rows]) == rk and _key(p.values[cols]) == ck]  # type: ignore[arg-type]
                cell_id = f"{rows}={rk}|{cols}={ck}" + (f"|{layer}={layer_key}" if layer else "")
                if not inside:
                    empty.append({"cell_id": cell_id, "row": rlabel, "col": clabel})
                    continue
                cells.append({
                    "cell_id": cell_id, "row": rlabel, "col": clabel, "count": len(inside),
                    "verified": sum(1 for p in inside if p.verified),
                    "papers": [{"source_id": str(p.source_id), "title": p.title, "extraction_id": str(p.extraction_id), "verified": p.verified} for p in inside],
                })
        return {
            "layer": layer_axis.labels[layer_key] if layer_axis and layer_key is not None else None,
            "n_papers": len(members), "cells": cells, "empty_cells": empty,
        }

    return {
        "rows": rows, "cols": cols, "layer": layer,
        "row_values": list(row_axis.labels.values()), "col_values": list(col_axis.labels.values()),
        "n_papers": len(papers), "n_placed": len(placed), "not_reported": not_reported,
        "matrices": [one(k) for k in layer_axis.labels] if layer_axis else [one(None)],
        "note": "An empty cell means no extracted paper in this project has that combination. It is not evidence that no research exists.",
    }


__all__ = ["DIMENSIONS", "Paper", "build_matrix", "dimension_value", "papers_for_project"]
