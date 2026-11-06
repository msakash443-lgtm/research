"""Load and validate the scholar-supplied gold set (X.15)."""
import json
from pathlib import Path

import jsonschema

HERE = Path(__file__).parent


def load_gold_set(path: Path | None = None) -> dict:
    data = json.loads((path or HERE / "gold_set.json").read_text(encoding="utf-8"))
    schema = json.loads((HERE / "gold_set.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(data, schema)
    ids = [p["id"] for p in data["papers"]]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate paper ids in gold set")
    return data


def csv_to_gold_set(csv_path: Path, topic: str, supplied_by: str,
                    extractions_csv: Path | None = None) -> dict:
    """Convert the scholar's filled-in CSV to the fixture shape.

    `extractions_csv` (columns id,field,value,source_quote; one row per field) is optional and
    attaches a verified extraction to the paper with that id.
    """
    import csv

    papers = []
    with csv_path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            papers.append({
                "id": row["id"].strip(),
                "doi": row.get("doi", "").strip() or None,
                "title": row["title"].strip(),
                "abstract": row.get("abstract", "").strip() or None,
                "label": row["label"].strip().lower(),
                "known_relevant": row.get("known_relevant", "").strip().lower() in ("1", "true", "yes"),
                "reason": row["reason"].strip(),
            })
    if extractions_csv is not None:
        by_id = {p["id"]: p for p in papers}
        with extractions_csv.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                paper = by_id.get(row["id"].strip())
                if paper is None:
                    raise ValueError(f"extraction row for unknown paper id {row['id']!r}")
                field = row["field"].strip()
                if not field:
                    continue
                fields = paper.setdefault("extraction", {})
                if field in fields:
                    raise ValueError(f"duplicate extraction field {field!r} for paper {paper['id']!r}")
                fields[field] = {"value": row["value"].strip(), "source_quote": row["source_quote"].strip()}
    return {"topic": topic, "supplied_by": supplied_by, "papers": papers}


def abstract_from_inverted_index(index: dict | None) -> str | None:
    """Rebuild OpenAlex's `abstract_inverted_index` ({word: [positions]}) as plain text."""
    if not index:
        return None
    slots = {pos: word for word, positions in index.items() for pos in positions}
    return " ".join(slots[i] for i in sorted(slots)) or None


def dataset_rows_to_gold_set(rows: list[dict], metadata: dict[str, dict], *, n_exclude: int,
                             seed: int, topic: str, supplied_by: str, source_note: str) -> dict:
    """Build the fixture from a published screening dataset (columns openalex_id, doi,
    label_included) plus fetched `metadata` ({openalex short id: {title, abstract}}).

    Every included record is kept; a seeded sample of `n_exclude` excluded records is added,
    skipping records whose metadata has no title. An included record without a title is an error,
    never silently dropped.
    """
    import random

    def short(row):
        return row["openalex_id"].rsplit("/", 1)[-1]

    def paper(row, label):
        meta = metadata[short(row)]
        return {
            "id": short(row),
            "doi": (row.get("doi") or "").strip() or None,
            "title": meta["title"].strip(),
            "abstract": meta.get("abstract"),
            "label": label,
            "known_relevant": label == "include",
            "reason": f"{source_note}: {label}d",
        }

    def has_title(row):
        if not row["openalex_id"].strip():
            return False
        return bool((metadata.get(short(row)) or {}).get("title", "").strip())

    seen: set[str] = set()
    unique = []
    for r in sorted(rows, key=lambda r: r["label_included"].strip() != "1"):  # includes win
        key = r["openalex_id"].strip()
        if key and key in seen:
            continue
        seen.add(key)
        unique.append(r)
    included = [r for r in unique if r["label_included"].strip() == "1"]
    excluded = [r for r in unique if r["label_included"].strip() == "0"]
    missing = [r["openalex_id"] or r.get("doi") or r.get("pmid") for r in included if not has_title(r)]
    if missing:
        raise ValueError(f"included records without a title in the fetched metadata: {missing}")
    random.Random(seed).shuffle(excluded)
    chosen = [r for r in excluded if has_title(r)][:n_exclude]
    if len(chosen) < n_exclude:
        raise ValueError(f"only {len(chosen)} excluded records have titles; {n_exclude} requested")
    papers = [paper(r, "include") for r in included] + [paper(r, "exclude") for r in chosen]
    return {"topic": topic, "supplied_by": supplied_by, "papers": papers}
