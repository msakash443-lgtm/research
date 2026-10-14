"""Writing provenance report (X.31.12), pure logic.

`build_report` turns span character counts and saved-version timestamps into the report's numbers. Nothing here
reads text: spans arrive as lengths and ids, so no manuscript or source text can reach the report. The AI-use
statement is filled from a pinned template file (`statements/ai_use_statement.v1.toml`); no model writes it.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from app.ledger import AI_ORIGINS

TEMPLATE_PATH = Path(__file__).parent / "statements" / "ai_use_statement.v1.toml"
UNKNOWN_SESSION = "unknown"
NOT_RECORDED = "not recorded"

SHARE_LABELS = {
    "typed": "Typed by authors",
    "quoted": "Quoted (with citation)",
    "pasted_own": "Pasted, confirmed own",
    "pasted_unresolved": "Pasted, unresolved",
    "ai": "AI-edited, accepted",
}

ACTION_LABELS = {
    "challenge": "Challenge my argument",
    "find_evidence": "Find evidence",
    "clearer_sentence": "Clearer sentence",
    "argument_check": "Argument check",
}

LIMITS = [
    "It records how the saved text was produced, counted in characters, as this workspace recorded it.",
    "It cannot see text written in another tool before it was pasted or imported.",
    "It records the signed-in account that saved each version. It does not prove who was at the keyboard.",
    "It is not an authorship, plagiarism or AI-detection verdict.",
]


def load_template(path: Path = TEMPLATE_PATH) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def template_label(template: dict[str, Any]) -> str:
    return f"{template['id']}@{template['version']}"


def _category(span: dict[str, Any]) -> str:
    if span["origin"] in AI_ORIGINS:
        return "ai"
    if span["origin"] == "pasted":
        return {"quotation": "quoted", "own": "pasted_own"}.get(span.get("paste"), "pasted_unresolved")
    return "typed"


def _pct(part: int, total: int) -> float:
    return round(part * 100 / total, 1) if total else 0.0


def _sessions(versions: Sequence[dict[str, Any]]) -> dict[str, Any]:
    by_session: dict[str, list[datetime]] = {}
    for version in versions:
        key = version["client_session_id"] or UNKNOWN_SESSION
        by_session.setdefault(key, []).append(version["created_at"])
    seconds = sum((max(times) - min(times)).total_seconds() for times in by_session.values())
    return {"count": len(by_session), "hours": round(seconds / 3600, 1), "versions_saved": len(versions)}


def _ai(spans: Sequence[dict[str, Any]], total: int) -> dict[str, Any]:
    items: dict[str, dict[str, Any]] = {}
    chars = 0
    for span in spans:
        if _category(span) != "ai":
            continue
        chars += span["chars"]
        key = span["suggestion_id"] or UNKNOWN_SESSION
        item = items.setdefault(key, {
            "suggestion_id": span["suggestion_id"], "action": span["action"], "model_id": span["model_id"],
            "prompt_version": span["prompt_version"], "chars_kept": 0,
        })
        item["chars_kept"] += span["chars"]
    ordered = sorted(items.values(), key=lambda item: (-item["chars_kept"], item["suggestion_id"] or ""))
    return {"chars": chars, "share_pct": _pct(chars, total), "items": ordered}


def build_report(
    scope: str,
    documents: Sequence[dict[str, Any]],
    spans: Sequence[dict[str, Any]],
    versions: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """The report's numbers. `spans` are the covered versions' spans; `versions` are the saved versions up to them."""
    total = sum(span["chars"] for span in spans)
    counts = {key: 0 for key in SHARE_LABELS}
    for span in spans:
        counts[_category(span)] += span["chars"]
    pasted = [
        {
            "document_id": span["document_id"], "span_id": span["span_id"],
            "status": span.get("paste") or "unconfirmed", "source_id": span.get("source_id"), "chars": span["chars"],
        }
        for span in spans
        if span["origin"] == "pasted"
    ]
    return {
        "scope": scope,
        "documents": [dict(document) for document in documents],
        "total_chars": total,
        "shares": {key: _pct(counts[key], total) for key in SHARE_LABELS},
        "sessions": _sessions(versions),
        "ai": _ai(spans, total),
        "pasted": pasted,
        "limits": list(LIMITS),
    }


def render_statement(report: dict[str, Any], template: dict[str, Any]) -> str:
    if report["ai"]["chars"] == 0:
        return template["without_ai"]
    models = sorted({item["model_id"] for item in report["ai"]["items"] if item["model_id"]})
    actions = sorted({ACTION_LABELS.get(item["action"], item["action"]) for item in report["ai"]["items"] if item["action"]})
    return template["with_ai"].format(
        ai_share_pct=f"{report['ai']['share_pct']:.1f}",
        models=", ".join(models) or "not recorded",
        actions=", ".join(actions) or "not recorded",
    )


def report_hash(report: dict[str, Any], statement: str) -> str:
    canonical = json.dumps({"report": report, "statement": statement}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def to_markdown(report: dict[str, Any], statement: str, label: str) -> str:
    lines = [
        "# Writing provenance report",
        "",
        f"Scope: {report['scope']}. Statement template: {label}.",
        f"Total saved characters counted: {report['total_chars']}.",
        "",
        "## How the saved text was produced",
        "",
        "| Kind | Share of characters |",
        "|---|---|",
    ]
    lines += [f"| {SHARE_LABELS[key]} | {report['shares'][key]:.1f}% |" for key in SHARE_LABELS]
    sessions = report["sessions"]
    lines += [
        "",
        f"Writing sessions: {sessions['count']} ({sessions['hours']} hours between first and last save in each).",
        "",
        "## AI contributions",
        "",
    ]
    lines += [
        f"- {ACTION_LABELS.get(item['action'], item['action']) if item['action'] else NOT_RECORDED} · "
        f"{item['model_id'] or NOT_RECORDED} · {item['prompt_version'] or NOT_RECORDED} · "
        f"{item['chars_kept']} characters kept"
        for item in report["ai"]["items"]
    ] or ["None recorded."]
    lines += ["", "## Pasted and quoted material", ""]
    lines += [
        f"- {item['status']} · {item['chars']} characters · "
        + (f"source {item['source_id']}" if item['source_id'] else "no source linked")
        for item in report["pasted"]
    ] or ["None recorded."]
    lines += ["", "## AI-use statement", "", statement, "", "## What this report cannot show", ""]
    lines += [f"- {limit}" for limit in report["limits"]]
    return "\n".join(lines) + "\n"
