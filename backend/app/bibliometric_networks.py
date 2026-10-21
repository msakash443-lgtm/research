"""Pure bibliometric networks from explicit, project-selected source records.

Callers must supply real cited-reference identifiers and author keywords. This
module never extracts them from titles, abstracts, or model-generated text.
"""

from __future__ import annotations

import csv
import io
from collections import Counter
from itertools import combinations

# Same spreadsheet formula-injection guard as replication_export.py and routers/audit.py.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_cell(value) -> str:
    cell = str(value)
    return "'" + cell if cell.startswith(_FORMULA_PREFIXES) else cell


def build(records: list[dict]) -> dict:
    """Count links once per citing source, using full counting."""
    sources = {}
    for record in records:
        source_id = record["source_id"]
        if not isinstance(source_id, str) or not source_id.strip() or source_id in sources:
            raise ValueError("Source IDs must be nonempty and unique")
        references = record.get("reference_ids")
        keywords = record.get("keywords")
        if not isinstance(references, list) or not isinstance(keywords, list):
            raise ValueError("References and keywords must be explicit lists")
        if any(not isinstance(item, str) or not item.strip() for item in references + keywords):
            raise ValueError("Reference IDs and keywords must be nonempty strings")
        sources[source_id] = {
            "title": record.get("title") or source_id,
            "references": set(references),
            "keywords": {keyword.strip().casefold() for keyword in keywords},
        }

    co_citation: Counter[tuple[str, str]] = Counter()
    coupling: Counter[tuple[str, str]] = Counter()
    co_occurrence: Counter[tuple[str, str]] = Counter()
    for source in sources.values():
        co_citation.update(combinations(sorted(source["references"]), 2))
        co_occurrence.update(combinations(sorted(source["keywords"]), 2))
    for left, right in combinations(sorted(sources), 2):
        shared = sources[left]["references"] & sources[right]["references"]
        if shared:
            coupling[left, right] = len(shared)

    def edges(counts):
        return [{"left": left, "right": right, "weight": count}
                for (left, right), count in sorted(counts.items())]

    return {
        "co_citation": edges(co_citation),
        "bibliographic_coupling": edges(coupling),
        "keyword_co_occurrence": edges(co_occurrence),
        "source_count": len(sources),
        "sources_with_references": sum(bool(source["references"]) for source in sources.values()),
        "sources_with_keywords": sum(bool(source["keywords"]) for source in sources.values()),
    }


def vosviewer_files(edges: list[dict], labels: dict[str, str] | None = None) -> tuple[str, str]:
    """Return VOSviewer map and network tab-delimited file contents."""
    nodes = sorted({node for edge in edges for node in (edge["left"], edge["right"])})
    node_ids = {node: index for index, node in enumerate(nodes, 1)}

    def table(rows):
        output = io.StringIO(newline="")
        writer = csv.writer(output, delimiter="\t", lineterminator="\n")
        writer.writerows(rows)
        return output.getvalue()

    map_text = table([["id", "label"], *[[node_ids[node], (labels or {}).get(node, node)] for node in nodes]])
    network_text = table([[node_ids[edge["left"]], node_ids[edge["right"]], edge["weight"]]
                          for edge in edges])
    return map_text, network_text


def citespace_csv(records: list[dict]) -> str:
    """Return bibliographic rows for CiteSpace's generic CSV conversion workflow.

    Missing bibliographic fields stay blank. This is converter input, not a
    fabricated Web of Science export.
    """
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["source_id", "title", "year", "authors", "doi", "keywords", "cited_references"])
    for record in records:
        writer.writerow([_csv_cell(cell) for cell in (
            record["source_id"], record.get("title") or "", record.get("year") or "",
            "; ".join(record.get("authors") or []), record.get("doi") or "",
            "; ".join(record.get("keywords") or []),
            "; ".join(record.get("reference_ids") or []),
        )])
    return output.getvalue()
