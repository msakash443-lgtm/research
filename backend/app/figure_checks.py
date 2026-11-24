"""Deterministic accuracy checks for figure specs (plan M8.6). Pure: a spec, its kind and the paper's FullText go in,
a list of findings comes out. No DB, no network.

`error` findings block export (M8.8), make a corrected spec invalid (M8.7), and make a generated flowchart invalid
(M8.5, via `STRUCTURAL_CODES`). `warning` findings are shown to the scholar and do not block anything.

Two codes beyond the plan's list: `no_start` and `no_end` (a flowchart needs one start and at least one end, per
M8.5). Reachability is only checked when there is exactly one start, because "reachable from start" is undefined
otherwise; the `multiple_starts` finding already covers that case.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass

from app import figure_spec
from app.fulltext import FullText

ERROR = "error"
WARNING = "warning"

# Labels longer than these read badly when rendered. A warning only: the text itself is still correct.
READABLE_NODE_LABEL = 60
READABLE_EDGE_LABEL = 30

STRUCTURAL_CODES = frozenset({"multiple_starts", "no_start", "no_end", "unreachable_node", "dangling_edge"})


@dataclass(frozen=True)
class Finding:
    code: str
    element_id: str | None
    severity: str
    message: str

    def as_dict(self) -> dict[str, str | None]:
        return asdict(self)


def check_figure(spec: dict, full: FullText, kind: str) -> list[Finding]:
    """Every deterministic finding for `spec` read as `kind`. Raises `figure_spec.SpecError` for a malformed spec."""
    clean = figure_spec.normalize(spec, kind)
    ids = {node["id"] for node in clean["nodes"]}
    findings = _quotes(clean, full) + _dangling(clean, ids) + _orphans(clean, ids) + _labels(clean)
    if kind == figure_spec.CONCEPTUAL_FRAMEWORK:
        findings += _mediation(clean)
    else:
        findings += _flowchart(clean, ids)
    return findings


def structural_errors(findings: list[Finding]) -> list[Finding]:
    """The findings that make a flowchart's structure invalid (M8.5)."""
    return [f for f in findings if f.severity == ERROR and f.code in STRUCTURAL_CODES]


def _edge_id(edge: dict) -> str:
    return f"{edge['from']}->{edge['to']}"


def _quotes(spec: dict, full: FullText) -> list[Finding]:
    out: list[Finding] = []
    elements = [(node["id"], node) for node in spec["nodes"]] + [(_edge_id(edge), edge) for edge in spec["edges"]]
    for element_id, item in elements:
        for evidence in item["evidence"]:
            if not full.find_quote(evidence["quote"]):
                out.append(Finding("quote_not_found", element_id, ERROR, "a quote for this element is not in the paper"))
    return out


def _dangling(spec: dict, ids: set[str]) -> list[Finding]:
    return [
        Finding("dangling_edge", _edge_id(edge), ERROR, "the edge refers to a node that is not in the figure")
        for edge in spec["edges"]
        if edge["from"] not in ids or edge["to"] not in ids
    ]


def _orphans(spec: dict, ids: set[str]) -> list[Finding]:
    touched = {end for edge in spec["edges"] if edge["from"] in ids and edge["to"] in ids for end in (edge["from"], edge["to"])}
    return [
        Finding("orphan_node", node["id"], WARNING, "no edge connects this node to the rest of the figure")
        for node in spec["nodes"]
        if node["id"] not in touched
    ]


def _labels(spec: dict) -> list[Finding]:
    out = [
        Finding("label_too_long", node["id"], WARNING, f"node label is over {READABLE_NODE_LABEL} characters")
        for node in spec["nodes"]
        if len(node["label"]) > READABLE_NODE_LABEL
    ]
    out += [
        Finding("label_too_long", _edge_id(edge), WARNING, f"edge label is over {READABLE_EDGE_LABEL} characters")
        for edge in spec["edges"]
        if len(edge["label"]) > READABLE_EDGE_LABEL
    ]
    return out


def _mediation(spec: dict) -> list[Finding]:
    has_mediator = any(node["role"] == "mediator" for node in spec["nodes"])
    if has_mediator:
        return []
    return [
        Finding("mediation_without_via", _edge_id(edge), ERROR, "a mediation edge needs a mediator node in the figure")
        for edge in spec["edges"]
        if edge["kind"] == "mediation"
    ]


def _flowchart(spec: dict, ids: set[str]) -> list[Finding]:
    out: list[Finding] = []
    starts = [node["id"] for node in spec["nodes"] if node["role"] == "start"]
    if not starts:
        out.append(Finding("no_start", None, ERROR, "a flowchart needs one start node"))
    elif len(starts) > 1:
        out += [Finding("multiple_starts", start, ERROR, "a flowchart has exactly one start node") for start in starts]
    if not any(node["role"] == "end" for node in spec["nodes"]):
        out.append(Finding("no_end", None, ERROR, "a flowchart needs at least one end node"))
    if len(starts) == 1:
        reached = _reachable(starts[0], spec["edges"], ids)
        out += [
            Finding("unreachable_node", node["id"], ERROR, "this node cannot be reached from the start")
            for node in spec["nodes"]
            if node["id"] not in reached
        ]
    return out


def _reachable(start: str, edges: list[dict], ids: set[str]) -> set[str]:
    following: dict[str, list[str]] = {}
    for edge in edges:
        if edge["from"] in ids and edge["to"] in ids:
            following.setdefault(edge["from"], []).append(edge["to"])
    seen = {start}
    queue = deque([start])
    while queue:
        for nxt in following.get(queue.popleft(), []):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen
