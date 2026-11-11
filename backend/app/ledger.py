"""Authorship ledger logic (X.31.9): pure functions over ProseMirror JSON, no database.

A document version is the editor's JSON. Every text run carries exactly one `prov` mark, and a span is all
the runs that share one `sid`. `diff_spans` says what changed between two versions; `check_integrity` lists
the rules a save must pass (each violation has a stable `code`); `detect_pasted` matches new pasted text against
the project's own sources. The save and restore routes in `routers/documents.py` do the database work.
"""

from __future__ import annotations

import copy
import hashlib
import json
import unicodedata
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.number_citation import resolve_number

ALLOWED_NODES = frozenset({"doc", "paragraph", "heading", "blockquote", "bullet_list", "ordered_list", "list_item", "hard_break", "text", "cite", "stat"})
ALLOWED_MARKS = frozenset({"em", "strong", "prov", "evidence"})
TEXT_BLOCKS = frozenset({"paragraph", "heading"})
ORIGINS = frozenset({"typed", "dictated", "pasted", "imported", "from_note", "ai_edited", "ai_suggested"})
AI_ORIGINS = frozenset({"ai_edited", "ai_suggested"})
PASTE_VALUES = frozenset({"unconfirmed", "own", "quotation"})
MAX_CONTENT_TEXT = 150_000
MIN_PASTE_MATCH_CHARS = 20
MIN_SUGGESTION_OVERLAP_CHARS = 40

EMPTY_DOCUMENT: dict[str, Any] = {"type": "doc", "content": []}


@dataclass(frozen=True)
class Problem:
    code: str
    message: str


@dataclass
class Span:
    sid: str
    origin: str
    by: str
    sug: str | None
    note: str | None
    paste: str | None
    src: str | None
    runs: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "".join(self.runs)


@dataclass(frozen=True)
class SpanChange:
    kind: str  # an AuthorshipKind value: inserted | removed | changed | resolved | restored
    span_id: str
    origin: str
    paste_status: str | None
    chars_before: int
    chars_after: int
    text_hash: str
    suggestion_id: str | None
    note_id: str | None


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def content_hash(document: dict[str, Any]) -> str:
    """sha256 of the canonical JSON (sorted keys, no spaces), so the same document always hashes the same."""
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256_text(canonical)


def normalise(text: str) -> str:
    """What "the same text" means for paste detection: Unicode-normalised, case-folded, single spaces."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def pin_stat_values(document: dict[str, Any], number_run_outputs: dict[str, Any] | None) -> dict[str, Any]:
    """A copy of `document` whose `stat` nodes carry the number the analysis run really holds as their display
    `value`. The editor shows `value`, so a client-sent value is never trusted: it is replaced by the resolved
    number, or dropped when the path does not resolve (check_integrity then refuses a new unresolved number)."""
    pinned = copy.deepcopy(document)
    runs = number_run_outputs or {}
    for node in _nodes(pinned):
        if node.get("type") != "stat":
            continue
        attrs = node.setdefault("attrs", {})
        number = resolve_number(runs.get(attrs.get("analysis_run_id")), attrs.get("path"))
        if number is None:
            attrs.pop("value", None)
        else:
            attrs["value"] = number
    return pinned


def _nodes(node: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield node
    for child in node.get("content", []) or []:
        if isinstance(child, dict):
            yield from _nodes(child)


def _textblocks(document: dict[str, Any]) -> list[dict[str, Any]]:
    return [node for node in _nodes(document) if node.get("type") in TEXT_BLOCKS]


def _prov_attrs(node: dict[str, Any]) -> dict[str, Any] | None:
    for mark in node.get("marks", []) or []:
        if isinstance(mark, dict) and mark.get("type") == "prov":
            return mark.get("attrs") or {}
    return None


def plain_text(document: dict[str, Any]) -> str:
    """The document's text, one line per text block (what the research tools and the size limit read)."""
    return "\n".join("".join(child.get("text", "") for child in block.get("content", []) if child.get("type") == "text")
                     for block in _textblocks(document))


def text_blocks(document: dict[str, Any]) -> list[tuple[str, bool, bool]]:
    """Each text block as (text, quoted, cited), in the order `plain_text` joins them, so offsets into
    `plain_text` line up with these texts. `quoted` is true if any run is a quotation; `cited` if it has a cite."""
    blocks = []
    for block in _textblocks(document):
        children = block.get("content", [])
        text = "".join(child.get("text", "") for child in children if child.get("type") == "text")
        quoted = any(_quoted(child) for child in children)
        cited = any(child.get("type") == "cite" for child in children)
        blocks.append((text, quoted, cited))
    return blocks


def validate_schema(document: dict[str, Any]) -> list[Problem]:
    """Structure only: allowed nodes and marks, one well-formed prov mark per text run, size limit."""
    if not isinstance(document, dict) or document.get("type") != "doc":
        return [Problem("invalid_schema", "the document root must be a doc")]
    problems: list[Problem] = []
    attrs_by_sid: dict[str, dict[str, Any]] = {}
    for node in _nodes(document):
        kind = node.get("type")
        if kind not in ALLOWED_NODES:
            problems.append(Problem("invalid_schema", f"node type {kind!r} is not allowed"))
            continue
        for mark in node.get("marks", []) or []:
            if not isinstance(mark, dict) or mark.get("type") not in ALLOWED_MARKS:
                problems.append(Problem("invalid_schema", f"mark {mark.get('type') if isinstance(mark, dict) else mark!r} is not allowed"))
        if kind == "cite" and not isinstance((node.get("attrs") or {}).get("source_id"), str):
            problems.append(Problem("invalid_schema", "a citation needs a source_id"))
        if kind == "stat":
            stat_attrs = node.get("attrs") or {}
            if not (isinstance(stat_attrs.get("analysis_run_id"), str) and isinstance(stat_attrs.get("path"), str)):
                problems.append(Problem("invalid_schema", "an inserted number needs an analysis_run_id and a path"))
        if kind != "text":
            continue
        if not isinstance(node.get("text"), str):
            problems.append(Problem("invalid_schema", "a text node needs its text"))
        provs = [m for m in node.get("marks", []) or [] if isinstance(m, dict) and m.get("type") == "prov"]
        if not provs:
            problems.append(Problem("missing_provenance", "every text run needs a provenance mark"))
            continue
        if len(provs) > 1:
            problems.append(Problem("invalid_schema", "a text run may carry only one provenance mark"))
            continue
        attrs = provs[0].get("attrs") or {}
        if not _valid_prov(attrs):
            problems.append(Problem("invalid_schema", "a provenance mark has an invalid origin, author or paste value"))
            continue
        previous = attrs_by_sid.setdefault(attrs["sid"], attrs)
        if previous != attrs:
            problems.append(Problem("invalid_schema", f"span {attrs['sid']} has runs with different provenance"))
    if len(plain_text(document)) > MAX_CONTENT_TEXT:
        problems.append(Problem("text_too_long", f"a section can hold at most {MAX_CONTENT_TEXT} characters"))
    return problems


def _valid_prov(attrs: dict[str, Any]) -> bool:
    optional_text = ("sug", "note", "src")
    if not (isinstance(attrs.get("sid"), str) and attrs["sid"]):
        return False
    if attrs.get("origin") not in ORIGINS or not isinstance(attrs.get("by"), str):
        return False
    if attrs.get("paste") is not None and attrs.get("paste") not in PASTE_VALUES:
        return False
    return all(attrs.get(key) is None or isinstance(attrs.get(key), str) for key in optional_text)


def collect_spans(document: dict[str, Any]) -> dict[str, Span]:
    """Spans by sid, in document order. Assumes the document passed `validate_schema`."""
    spans: dict[str, Span] = {}
    for node in _nodes(document):
        if node.get("type") != "text":
            continue
        attrs = _prov_attrs(node) or {}
        span = spans.get(attrs.get("sid"))
        if span is None:
            span = Span(
                sid=attrs["sid"], origin=attrs["origin"], by=attrs["by"], sug=attrs.get("sug"), note=attrs.get("note"),
                paste=attrs.get("paste"), src=attrs.get("src"),
            )
            spans[span.sid] = span
        span.runs.append(node["text"])
    return spans


def diff_spans(previous: dict[str, Any], new: dict[str, Any], restored: dict[str, Any] | None = None) -> list[SpanChange]:
    """One change per span that differs. `restored` is the version a restore copied from: a span it contains
    that the current version lacks is `restored` rather than `inserted`."""
    before = collect_spans(previous)
    after = collect_spans(new)
    restored_ids = set(collect_spans(restored)) if restored is not None else set()
    changes: list[SpanChange] = []
    for sid in sorted(before.keys() | after.keys()):
        old, current = before.get(sid), after.get(sid)
        if old is not None and current is None:
            kind = "removed"
        elif old is None and current is not None:
            kind = "restored" if sid in restored_ids else "inserted"
        elif old.text != current.text:
            kind = "changed"
        elif old.paste != current.paste:
            kind = "resolved"
        else:
            continue
        span = current or old
        changes.append(SpanChange(
            kind=kind, span_id=sid, origin=span.origin, paste_status=span.paste,
            chars_before=len(old.text) if old else 0, chars_after=len(current.text) if current else 0,
            text_hash=sha256_text(span.text), suggestion_id=span.sug, note_id=span.note,
        ))
    return changes


def check_integrity(
    previous: dict[str, Any],
    new: dict[str, Any],
    *,
    actor: str,
    accepted: dict[str, str],
    suggestion_texts: Sequence[str],
    citable_source_ids: Iterable[str],
    number_run_outputs: dict[str, Any] | None = None,
    restored: dict[str, Any] | None = None,
    ai_wording_allowed: bool = True,
) -> list[Problem]:
    """The rules a save must pass. `accepted` maps accepted suggestion ids to the text a person accepted;
    `suggestion_texts` is every AI output for this document; `citable_source_ids` are the project's verified,
    unmerged sources; `number_run_outputs` maps this project's `AnalysisRun` ids to their stored `output_json`.
    `ai_wording_allowed` is false below the Full AI level (Q27): a new AI span is then refused, while AI spans
    already saved or brought back by a restore are kept. Empty list means the save may be written."""
    before = collect_spans(previous)
    after = collect_spans(new)
    restored_ids = set(collect_spans(restored)) if restored is not None else set()
    citable = set(citable_source_ids)
    runs = number_run_outputs or {}
    problems: list[Problem] = []

    for sid, old in before.items():
        current = after.get(sid)
        if current is not None and (old.origin, old.by, old.sug, old.note) != (current.origin, current.by, current.sug, current.note):
            problems.append(Problem("provenance_changed", f"span {sid}: origin, author, suggestion or note was changed"))

    for sid, span in after.items():
        if sid not in before and sid not in restored_ids and span.by != actor:
            problems.append(Problem("foreign_author", f"span {sid} was written by someone else"))
        if span.origin in AI_ORIGINS:
            problems.extend(_ai_span_problems(span, accepted))
            if not ai_wording_allowed and sid not in before and sid not in restored_ids:
                problems.append(Problem("ai_wording_not_allowed", f"AI span {sid} needs the Full AI level"))

    new_spans = [span for sid, span in after.items() if sid not in before]
    for span in new_spans:
        if span.origin not in AI_ORIGINS and _copies_a_suggestion(span.text, suggestion_texts):
            problems.append(Problem("ai_text_relabelled", f"span {span.sid} copies AI output but is not marked as AI"))

    for block in _textblocks(new):
        quoted = {_prov_attrs(n)["sid"] for n in block.get("content", []) if _quoted(n)}
        has_cite = any(n.get("type") == "cite" for n in block.get("content", []))
        if quoted and not has_cite:
            problems.append(Problem("quotation_without_citation", "a quotation needs a citation in the same paragraph"))

    known_cites = {n["attrs"]["source_id"] for n in _nodes(previous) if n.get("type") == "cite"}
    for node in _nodes(new):
        if node.get("type") == "cite" and node["attrs"]["source_id"] not in known_cites | citable:
            problems.append(Problem("uncited_source", f"source {node['attrs']['source_id']} is not a verified project source"))

    known_stats = {(n["attrs"]["analysis_run_id"], n["attrs"]["path"]) for n in _nodes(previous) if n.get("type") == "stat"}
    for node in _nodes(new):
        if node.get("type") != "stat":
            continue
        run_id, path = node["attrs"]["analysis_run_id"], node["attrs"]["path"]
        if (run_id, path) in known_stats:
            continue
        if resolve_number(runs.get(run_id), path) is None:
            problems.append(Problem("unresolved_number", f"path {path!r} is not a number in analysis run {run_id}'s output"))
    return _unique(problems)


def _quoted(node: dict[str, Any]) -> bool:
    return node.get("type") == "text" and (_prov_attrs(node) or {}).get("paste") == "quotation"


def _ai_span_problems(span: Span, accepted: dict[str, str]) -> list[Problem]:
    if span.sug not in accepted:
        return [Problem("ai_text_without_accept", f"AI span {span.sid} has no accepted suggestion")]
    accepted_text = accepted[span.sug]
    if any(run not in accepted_text for run in span.runs):
        return [Problem("ai_text_mismatch", f"AI span {span.sid} differs from the text that was accepted")]
    return []


def _copies_a_suggestion(text: str, suggestion_texts: Sequence[str]) -> bool:
    haystack = normalise(text)
    for suggestion in suggestion_texts:
        needle = normalise(suggestion)
        if len(needle) < MIN_SUGGESTION_OVERLAP_CHARS:
            continue
        if any(needle[i:i + MIN_SUGGESTION_OVERLAP_CHARS] in haystack for i in range(len(needle) - MIN_SUGGESTION_OVERLAP_CHARS + 1)):
            return True
    return False


def _unique(problems: list[Problem]) -> list[Problem]:
    seen: set[tuple[str, str]] = set()
    unique: list[Problem] = []
    for problem in problems:
        if (problem.code, problem.message) not in seen:
            seen.add((problem.code, problem.message))
            unique.append(problem)
    return unique


def detect_pasted(
    previous: dict[str, Any], new: dict[str, Any], candidates: Sequence[tuple[str, str, str]]
) -> dict[str, tuple[str, str]]:
    """For each new pasted span of at least MIN_PASTE_MATCH_CHARS normalised characters that appears verbatim
    (after normalising) in a candidate, map its sid to (kind, id). `candidates` are (kind, id, text), checked in
    order, so the first match wins."""
    before = collect_spans(previous)
    normalised = [(kind, cid, normalise(text)) for kind, cid, text in candidates if text]
    matches: dict[str, tuple[str, str]] = {}
    for sid, span in collect_spans(new).items():
        if sid in before or span.origin != "pasted":
            continue
        needle = normalise(span.text)
        if len(needle) < MIN_PASTE_MATCH_CHARS:
            continue
        for kind, cid, haystack in normalised:
            if needle in haystack:
                matches[sid] = (kind, cid)
                break
    return matches
