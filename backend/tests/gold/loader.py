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


def csv_to_gold_set(csv_path: Path, topic: str, supplied_by: str) -> dict:
    """Convert the scholar's filled-in CSV to the fixture shape."""
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
    return {"topic": topic, "supplied_by": supplied_by, "papers": papers}
