"""Research-plan document (X.194): plain data in, a plan with one entry per section out. No database, no model.

Every section carries `written`. A section with nothing recorded says so (`written: false` and `NOT_WRITTEN`
in the Markdown) instead of inventing content. Nothing here is a decision: it only lays out what people saved.
"""

from __future__ import annotations

from typing import Any

NOT_WRITTEN = "Not written yet."

SECTIONS = ("idea_brief", "landscape_scan", "gaps", "research_questions", "design", "validity_plan", "analysis_plan", "milestones")

DESIGN_NOTE = "No design choice is stored: method and sample-size recommendations are computed on request and not saved."
VALIDITY_NOTE = "No validity and reliability plan is stored: the template is computed on request and not saved."


def build(
    *,
    idea_brief: dict[str, Any] | None,
    landscape_scan: dict[str, Any] | None,
    gaps: list[dict[str, Any]],
    questions: list[dict[str, Any]],
    objectives: list[dict[str, Any]],
    hypotheses: list[dict[str, Any]],
    analysis_plans: list[dict[str, Any]],
    milestones: list[dict[str, Any]],
) -> dict[str, Any]:
    """`questions`/`objectives`/`hypotheses` are flat rows; each question gets its own objectives and hypotheses
    by `research_question_id`. Objectives with no question are listed under `project_objectives`."""
    by_question: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for key, rows in (("objectives", objectives), ("hypotheses", hypotheses)):
        for row in rows:
            if row["research_question_id"] is not None:
                by_question.setdefault(str(row["research_question_id"]), {"objectives": [], "hypotheses": []})[key].append(row)
    linked = [
        {**question, "objectives": by_question.get(str(question["id"]), {}).get("objectives", []), "hypotheses": by_question.get(str(question["id"]), {}).get("hypotheses", [])}
        for question in questions
    ]
    project_objectives = [row for row in objectives if row["research_question_id"] is None]
    return {
        "idea_brief": {"written": idea_brief is not None, **(idea_brief or {})},
        "landscape_scan": {"written": landscape_scan is not None, **(landscape_scan or {})},
        "gaps": {"written": bool(gaps), "items": gaps},
        "research_questions": {
            "written": bool(questions) or bool(project_objectives),
            "items": linked,
            "project_objectives": project_objectives,
        },
        "design": {"written": False, "note": DESIGN_NOTE},
        "validity_plan": {"written": False, "note": VALIDITY_NOTE},
        "analysis_plan": {"written": bool(analysis_plans), "items": analysis_plans},
        "milestones": {"written": bool(milestones), "items": milestones},
    }


def _text(value: Any) -> str:
    return str(value).strip() if value not in (None, "") else "-"


def to_markdown(plan: dict[str, Any], title: str) -> str:
    lines = [f"# Research plan: {title}", ""]

    def section(heading: str, key: str) -> dict[str, Any]:
        lines.extend([f"## {heading}", ""])
        data = plan[key]
        if not data["written"]:
            lines.extend([data.get("note", NOT_WRITTEN), ""])
        return data

    data = section("Idea brief", "idea_brief")
    if data["written"]:
        lines.extend([f"- Problem: {_text(data.get('problem'))}", f"- Why it matters: {_text(data.get('why_it_matters'))}", f"- Risks: {_text(data.get('risks'))}", ""])

    data = section("Landscape scan", "landscape_scan")
    if data["written"]:
        lines.extend([f"- Scan `{data['id']}`: query \"{data['query']}\", {data['record_count']} records, run {data['run_at']}"])
        lines.extend(f"  - {s['service']}: {s['status']}" for s in data["services"])
        lines.append("")

    data = section("Gaps", "gaps")
    for gap in data.get("items", []):
        lines.append(f"- `{gap['id']}` ({gap['type']}): {gap['statement']} Sources: {', '.join(gap['supporting_paper_ids']) or '-'}")
    if data["written"]:
        lines.append("")

    data = section("Research questions, objectives and hypotheses", "research_questions")
    for question in data.get("items", []):
        lines.append(f"- Question `{question['id']}`: {question['text']} Gaps: {', '.join(question['gap_ids']) or '-'}")
        lines.extend(f"  - Objective `{o['id']}`: {o['statement']} (measure: {_text(o['measure'])}; deliverable: {_text(o['deliverable'])})" for o in question["objectives"])
        lines.extend(f"  - Hypothesis `{h['id']}` ({h['direction']}): {h['statement']}" for h in question["hypotheses"])
    lines.extend(f"- Objective `{o['id']}` (no question linked): {o['statement']}" for o in data.get("project_objectives", []))
    if data["written"]:
        lines.append("")

    section("Chosen design", "design")
    section("Validity and reliability plan", "validity_plan")

    data = section("Locked analysis plan", "analysis_plan")
    lines.extend(f"- `{p['id']}` locked {p['locked_at']} by {p['locked_by']}, plan hash {p['plan_sha256']}" for p in data.get("items", []))
    if data["written"]:
        lines.append("")

    data = section("Milestones", "milestones")
    for m in data.get("items", []):
        gate = f" - gate {m['gate']}: {m['gate_status']}" if m["gate"] else ""
        lines.append(f"- {m['stage']}{' (current)' if m['current'] else ''}{gate}")
    return "\n".join(lines).rstrip() + "\n"
