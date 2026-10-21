"""Per-section AI-use disclosure statements in journal-style wordings (X.78). Pure: no DB, no network, no model.

Input is the plain span rows the provenance report is built from (`app.provenance_store.span_rows_for`: lengths and
ids only, never manuscript text) and the covered sections. A span counts as AI when its origin is in `ledger.AI_ORIGINS`
(accepted AI output), the same rule the writing provenance report uses. Every figure is computed here; the wording
comes from `statements/ai_disclosure_formats.v1.toml`, which is meant to be replaced when a journal wants other text.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, Sequence

from app.ledger import AI_ORIGINS
from app.provenance_report import ACTION_LABELS, NOT_RECORDED

TEMPLATE_PATH = Path(__file__).parent / "statements" / "ai_disclosure_formats.v1.toml"

# What each recorded assistance action was for, in a phrase that follows "in order to" / "to".
PURPOSES = {
    "challenge": "challenge their arguments",
    "find_evidence": "find evidence in their own sources",
    "clearer_sentence": "suggest clearer wording",
    "argument_check": "check the structure of their arguments",
}


def load_template(path: Path = TEMPLATE_PATH) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def style_names(template: dict[str, Any] | None = None) -> list[str]:
    return sorted((template or load_template())["styles"])


def template_label(template: dict[str, Any]) -> str:
    return f"{template['id']}@{template['version']}"


def _join(items: Sequence[str]) -> str:
    items = list(items)
    if not items:
        return NOT_RECORDED
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def _purpose(action: str | None) -> str:
    if not action:
        return "assist with writing (action not recorded)"
    return PURPOSES.get(action, f"assist with writing (recorded action: {ACTION_LABELS.get(action, action)})")


def per_section(covered: Sequence[dict[str, Any]], spans: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per covered section, in the given order: characters, accepted-AI characters and share, and which
    models and kinds of assistance produced them."""
    out = []
    for section in covered:
        mine = [span for span in spans if span["document_id"] == section["document_id"]]
        total = sum(span["chars"] for span in mine)
        ai = [span for span in mine if span["origin"] in AI_ORIGINS]
        ai_chars = sum(span["chars"] for span in ai)
        out.append({
            "document_id": section["document_id"], "title": section["title"], "chars": total, "ai_chars": ai_chars,
            "ai_share_pct": round(ai_chars * 100 / total, 1) if total else 0.0,
            "models": sorted({span["model_id"] for span in ai if span.get("model_id")}),
            "purposes": sorted({_purpose(span.get("action")) for span in ai}),
        })
    return out


def render(sections: Sequence[dict[str, Any]], style: str, template: dict[str, Any] | None = None) -> dict[str, Any]:
    """The statement for `style`: {style, label, heading, statement, sections: [{document_id, title, text}], basis,
    template}. Raises `KeyError` for an unknown style."""
    template = template or load_template()
    wording = template["styles"][style]
    with_ai = [s for s in sections if s["ai_chars"] > 0]
    if with_ai:
        statement = wording["with_ai"].format(
            tools=_join(sorted({m for s in with_ai for m in s["models"]})),
            purposes=_join(sorted({p for s in with_ai for p in s["purposes"]})),
            sections=_join([s["title"] for s in with_ai]), n_ai=len(with_ai), n_all=len(sections),
        )
    else:
        statement = wording["without_ai"].format(n_all=len(sections))
    lines = []
    for s in sections:
        if s["ai_chars"] > 0:
            text = wording["section_with"].format(
                title=s["title"], share=f"{s['ai_share_pct']:.1f}", models=_join(s["models"]), purposes=_join(s["purposes"]))
        else:
            text = wording["section_without"].format(title=s["title"])
        lines.append({"document_id": s["document_id"], "title": s["title"], "text": text})
    return {
        "style": style, "label": wording["label"], "heading": wording["heading"], "statement": statement,
        "sections": lines, "basis": template["basis"], "template": template_label(template),
    }


def to_text(rendered: dict[str, Any]) -> str:
    out = [rendered["heading"], "", rendered["statement"], "", "By section:"]
    out += [f"- {line['text']}" for line in rendered["sections"]]
    out += ["", rendered["basis"],
            f"Wording: {rendered['label']} ({rendered['template']}). It is a template: check the target journal's current policy."]
    return "\n".join(out) + "\n"
