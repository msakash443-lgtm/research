"""Figure specs (plan M8.3): the JSON a figure is stored as, its JSON Schema, normalization, diff and hash.

Pure: no database, no rendering. A spec is `{"nodes": [...], "edges": [...]}`:
- node: `{id, label, role, evidence}`; `role` depends on the figure kind;
- edge: `{from, to, kind, label, evidence}`; `kind` depends on the figure kind;
- evidence: `[{quote, section}]`, the verbatim passages a node or edge rests on (verified later, M8.6).

Ids are kept exactly as the author gave them. Normalization never renumbers them, so a figure's history keeps
referring to the same nodes. Normalization does not drop an edge whose endpoint is missing: that is a finding
(`dangling_edge`, M8.6), not something to hide. It raises `SpecError` for anything malformed or over a cap.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

CONCEPTUAL_FRAMEWORK = "conceptual_framework"
METHODOLOGY_FLOWCHART = "methodology_flowchart"
KINDS = (CONCEPTUAL_FRAMEWORK, METHODOLOGY_FLOWCHART)

MAX_NODES = 40
MAX_EDGES = 80
MAX_NODE_LABEL = 80
MAX_EDGE_LABEL = 40
MAX_QUOTE = 300
MAX_SECTION = 200
MAX_EVIDENCE_PER_ITEM = 5
MAX_ID = 40

_ID_PATTERN = r"^[A-Za-z0-9_-]{1,40}$"
_ID = re.compile(_ID_PATTERN)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

_ROLES = {
    CONCEPTUAL_FRAMEWORK: ("independent", "dependent", "mediator", "moderator", "control"),
    METHODOLOGY_FLOWCHART: ("start", "process", "decision", "data", "end"),
}
_EDGE_KINDS = {
    CONCEPTUAL_FRAMEWORK: ("direct", "mediation", "moderation"),
    METHODOLOGY_FLOWCHART: ("sequence",),
}
_NODE_KEYS = frozenset({"id", "label", "role", "evidence"})
_EDGE_KEYS = frozenset({"from", "to", "kind", "label", "evidence"})
_EVIDENCE_KEYS = frozenset({"quote", "section"})


class SpecError(ValueError):
    """The spec is not a valid figure spec of its kind. The message names the first problem found."""


def _check_kind(kind: str) -> str:
    if kind not in KINDS:
        raise SpecError(f"unknown figure kind {kind!r}")
    return kind


def roles(kind: str) -> tuple[str, ...]:
    return _ROLES[_check_kind(kind)]


def edge_kinds(kind: str) -> tuple[str, ...]:
    return _EDGE_KINDS[_check_kind(kind)]


def schema(kind: str) -> dict[str, Any]:
    """The JSON Schema a model reply must satisfy (passed to `complete_json`, M8.4)."""
    evidence = {
        "type": "array",
        "maxItems": MAX_EVIDENCE_PER_ITEM,
        "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["quote", "section"],
            "properties": {
                "quote": {"type": "string", "minLength": 1, "maxLength": MAX_QUOTE},
                "section": {"type": ["string", "null"], "maxLength": MAX_SECTION},
            },
        },
    }
    node = {
        "type": "object",
        "additionalProperties": False,
        "required": sorted(_NODE_KEYS),
        "properties": {
            "id": {"type": "string", "pattern": _ID_PATTERN},
            "label": {"type": "string", "minLength": 1, "maxLength": MAX_NODE_LABEL},
            "role": {"type": "string", "enum": list(roles(kind))},
            "evidence": evidence,
        },
    }
    edge = {
        "type": "object",
        "additionalProperties": False,
        "required": sorted(_EDGE_KEYS),
        "properties": {
            "from": {"type": "string", "pattern": _ID_PATTERN},
            "to": {"type": "string", "pattern": _ID_PATTERN},
            "kind": {"type": "string", "enum": list(edge_kinds(kind))},
            "label": {"type": "string", "maxLength": MAX_EDGE_LABEL},
            "evidence": evidence,
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["nodes", "edges"],
        "properties": {
            "nodes": {"type": "array", "maxItems": MAX_NODES, "items": node},
            "edges": {"type": "array", "maxItems": MAX_EDGES, "items": edge},
        },
    }


def normalize(spec: Any, kind: str) -> dict[str, Any]:
    """Return the canonical form of `spec`: strings trimmed, duplicate edges removed (first kept), order kept.

    Raises `SpecError` for a wrong shape, an unknown role or edge kind, an empty or over-long text, a control
    character, a duplicate node id, a malformed id, or more nodes/edges than the caps allow."""
    _check_kind(kind)
    if not isinstance(spec, dict) or set(spec) != {"nodes", "edges"}:
        raise SpecError('spec must be an object with exactly the keys "nodes" and "edges"')
    raw_nodes, raw_edges = spec["nodes"], spec["edges"]
    if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
        raise SpecError('"nodes" and "edges" must be lists')
    if len(raw_nodes) > MAX_NODES:
        raise SpecError(f"a figure has at most {MAX_NODES} nodes")
    if len(raw_edges) > MAX_EDGES:
        raise SpecError(f"a figure has at most {MAX_EDGES} edges")

    nodes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_nodes:
        node = _node(raw, kind)
        if node["id"] in seen:
            raise SpecError(f"duplicate node id {node['id']!r}")
        seen.add(node["id"])
        nodes.append(node)

    edges: list[dict[str, Any]] = []
    keys: set[tuple[str, str, str, str]] = set()
    for raw in raw_edges:
        edge = _edge(raw, kind)
        key = (edge["from"], edge["to"], edge["kind"], edge["label"])
        if key in keys:
            continue
        keys.add(key)
        edges.append(edge)
    return {"nodes": nodes, "edges": edges}


def _node(raw: Any, kind: str) -> dict[str, Any]:
    _exact_keys(raw, _NODE_KEYS, "node")
    role = _text(raw["role"], "node role", 40)
    if role not in roles(kind):
        raise SpecError(f"node role {role!r} is not one of {', '.join(roles(kind))}")
    return {
        "id": _id(raw["id"], "node id"),
        "label": _text(raw["label"], "node label", MAX_NODE_LABEL),
        "role": role,
        "evidence": _evidence(raw["evidence"]),
    }


def _edge(raw: Any, kind: str) -> dict[str, Any]:
    _exact_keys(raw, _EDGE_KEYS, "edge")
    edge_kind = _text(raw["kind"], "edge kind", 40)
    if edge_kind not in edge_kinds(kind):
        raise SpecError(f"edge kind {edge_kind!r} is not one of {', '.join(edge_kinds(kind))}")
    return {
        "from": _id(raw["from"], "edge from"),
        "to": _id(raw["to"], "edge to"),
        "kind": edge_kind,
        "label": _text(raw["label"], "edge label", MAX_EDGE_LABEL, allow_empty=True),
        "evidence": _evidence(raw["evidence"]),
    }


def _exact_keys(raw: Any, keys: frozenset[str], what: str) -> None:
    if not isinstance(raw, dict) or set(raw) != keys:
        raise SpecError(f"each {what} must be an object with exactly the keys {sorted(keys)}")


def _text(value: Any, what: str, limit: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise SpecError(f"{what} must be a string")
    text = value.strip()
    if not text and not allow_empty:
        raise SpecError(f"{what} must not be empty")
    if len(text) > limit:
        raise SpecError(f"{what} is longer than {limit} characters")
    if _CONTROL.search(text):
        raise SpecError(f"{what} contains a control character")
    return text


def _id(value: Any, what: str) -> str:
    text = _text(value, what, MAX_ID)
    if not _ID.match(text):
        raise SpecError(f"{what} {text!r} must be 1-{MAX_ID} letters, digits, '_' or '-'")
    return text


def _evidence(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise SpecError("evidence must be a list")
    if len(raw) > MAX_EVIDENCE_PER_ITEM:
        raise SpecError(f"an item has at most {MAX_EVIDENCE_PER_ITEM} evidence entries")
    out = []
    for item in raw:
        _exact_keys(item, _EVIDENCE_KEYS, "evidence entry")
        section = item["section"]
        if section is not None:
            section = _text(section, "evidence section", MAX_SECTION, allow_empty=True) or None
        out.append({"quote": _text(item["quote"], "evidence quote", MAX_QUOTE), "section": section})
    return out


def diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, list[str]]:
    """What changed between two normalized specs, by node id and by edge key. Lists are sorted."""
    old_nodes = {node["id"]: node for node in before["nodes"]}
    new_nodes = {node["id"]: node for node in after["nodes"]}
    old_edges = {_edge_key(edge) for edge in before["edges"]}
    new_edges = {_edge_key(edge) for edge in after["edges"]}
    return {
        "added_nodes": sorted(new_nodes.keys() - old_nodes.keys()),
        "removed_nodes": sorted(old_nodes.keys() - new_nodes.keys()),
        "changed_nodes": sorted(i for i in old_nodes.keys() & new_nodes.keys() if old_nodes[i] != new_nodes[i]),
        "added_edges": sorted(new_edges - old_edges),
        "removed_edges": sorted(old_edges - new_edges),
    }


def _edge_key(edge: dict[str, Any]) -> str:
    return f"{edge['from']} -> {edge['to']} [{edge['kind']}] {edge['label']}"


def spec_hash(spec: Any, kind: str) -> str:
    """SHA-256 of the canonical JSON of the normalized spec. Equal specs hash equal, whatever their whitespace."""
    canonical = normalize(spec, kind)
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
