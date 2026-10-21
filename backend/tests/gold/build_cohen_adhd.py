"""Build gold_set.json from the Cohen 2006 ADHD review ids file (plan X.15).

Usage: python -m tests.gold.build_cohen_adhd <Cohen_2006_ADHD_ids.csv>   (from backend/)
Writes gold_set.cohen_adhd.json, NOT gold_set.json: a populated gold_set.json requires a real
screening evaluation baseline (X.5 rule). Fetches titles/abstracts from the public OpenAlex API; writes nothing invented.
"""
import csv
import json
import sys
from pathlib import Path

import httpx

from tests.gold.loader import HERE, abstract_from_inverted_index, dataset_rows_to_gold_set, load_gold_set

SEED = 15
N_EXCLUDE = 50


def fetch_metadata(ids: list[str]) -> dict[str, dict]:
    out = {}
    with httpx.Client(timeout=60) as client:
        for i in range(0, len(ids), 50):
            batch = ids[i:i + 50]
            r = client.get("https://api.openalex.org/works", params={
                "filter": "openalex_id:" + "|".join(batch), "per-page": 50,
                "select": "id,title,abstract_inverted_index"})
            r.raise_for_status()
            for w in r.json()["results"]:
                out[w["id"].rsplit("/", 1)[-1]] = {
                    "title": w.get("title") or "",
                    "abstract": abstract_from_inverted_index(w.get("abstract_inverted_index"))}
    return out


def resolve_missing_ids(rows: list[dict]) -> None:
    """Fill in openalex_id from the PMID for rows the dataset left without one."""
    with httpx.Client(timeout=60) as client:
        for row in rows:
            if row["openalex_id"].strip() or not row["pmid"].strip():
                continue
            pmid = row["pmid"].rsplit("/", 1)[-1]
            r = client.get(f"https://api.openalex.org/works/pmid:{pmid}", params={"select": "id"})
            if r.status_code == 200:
                row["openalex_id"] = r.json()["id"]


def main(csv_path: str) -> None:
    with open(csv_path, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    resolve_missing_ids(rows)
    ids = [r["openalex_id"].rsplit("/", 1)[-1] for r in rows if r["openalex_id"].strip()]
    data = dataset_rows_to_gold_set(
        rows, fetch_metadata(ids), n_exclude=N_EXCLUDE, seed=SEED,
        topic="ADHD drug-class systematic review (Cohen et al. 2006)",
        supplied_by="Cohen et al. 2006 published screening decisions (via ASReview SYNERGY, CC0)",
        source_note="Cohen 2006 ADHD dataset label")
    (HERE / "gold_set.cohen_adhd.json").write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    load_gold_set(HERE / "gold_set.cohen_adhd.json")
    print(len(data["papers"]), "papers written")


if __name__ == "__main__":
    main(sys.argv[1])
