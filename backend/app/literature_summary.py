"""Deterministic literature-review summary (X.200). No language model: every line is a stored fact.

Built only from human-verified extractions of sources that are metadata-verified, not retracted and (when the
project is peer-reviewed-only, X.196) not preprints. Every other source in the evidence matrix is listed under
`excluded` with its reason and never appears in the body. Each statement carries source id, quote and page.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.contradictions import detect
from app.evidence_matrix import build as build_matrix
from app.models import Cluster, ClusterRun, Gap, Project, Source
from app.source_flags import is_preprint

REASONS = {
    "not_extracted": "no extraction yet",
    "extraction_not_verified": "its extraction has not been verified by a person",
    "metadata_unverified": "its citation metadata is not verified",
    "retracted": "retracted",
    "preprint_peer_reviewed_only": "preprint, and this project allows peer-reviewed sources only",
}
UNCLUSTERED = "Not in a thematic cluster"


def exclusion_reason(paper: dict, source: Source | None, peer_reviewed_only: bool) -> str | None:
    """Why a paper stays out of the summary body, or None when it may appear. Checked in a fixed order."""
    if source is None or "retracted" in (source.quality_flags or []):
        return "retracted" if source is not None else "metadata_unverified"
    if not source.metadata_verified:
        return "metadata_unverified"
    if peer_reviewed_only and is_preprint(source):
        return "preprint_peer_reviewed_only"
    if paper["extraction_id"] is None:
        return "not_extracted"
    if not paper["verified_by_human"]:
        return "extraction_not_verified"
    return None


def _statements(paper: dict) -> list[dict]:
    cell = paper["cells"].get("key_findings", {})
    if not cell.get("quote") or not isinstance(cell.get("value"), list):
        return []
    out = []
    for finding in cell["value"]:
        text = finding.get("statement") if isinstance(finding, dict) else finding
        if isinstance(text, str) and text.strip():
            out.append({
                "source_id": paper["source_id"], "title": paper["title"], "extraction_id": paper["extraction_id"],
                "statement": text.strip(), "quote": cell["quote"], "page": cell.get("page"),
            })
    return out


def _latest_clusters(db: Session, project: Project) -> list[Cluster]:
    run = db.scalar(
        select(ClusterRun).where(ClusterRun.project_id == project.id).order_by(ClusterRun.created_at.desc())
        .options(selectinload(ClusterRun.clusters).selectinload(Cluster.members)).limit(1)
    )
    return list(run.clusters) if run else []


def build(db: Session, project: Project, schema_name: str = "default") -> dict:
    matrix = build_matrix(db, project, schema_name)
    ids = {paper["source_id"] for paper in matrix["papers"]}
    sources = {str(s.id): s for s in db.scalars(select(Source).where(Source.id.in_(list(ids)))).all()} if ids else {}
    eligible, excluded = [], []
    for paper in matrix["papers"]:
        reason = exclusion_reason(paper, sources.get(paper["source_id"]), project.peer_reviewed_only)
        if reason is None:
            eligible.append(paper)
        else:
            excluded.append({"source_id": paper["source_id"], "title": paper["title"], "reason": reason,
                             "reason_text": REASONS[reason]})
    eligible_ids = {paper["source_id"] for paper in eligible}
    by_source = {paper["source_id"]: _statements(paper) for paper in eligible}

    themes, placed = [], set()
    for cluster in _latest_clusters(db, project):
        members = [str(m.source_id) for m in cluster.members if str(m.source_id) in eligible_ids]
        findings = [s for sid in members for s in by_source[sid]]
        placed.update(members)
        if findings:
            themes.append({"cluster_id": str(cluster.id), "label": cluster.label, "findings": findings})
    rest = [s for sid in sorted(eligible_ids - placed) for s in by_source[sid]]
    if rest:
        themes.append({"cluster_id": None, "label": UNCLUSTERED, "findings": rest})

    comparison = detect({"papers": eligible})
    titles = {paper["source_id"]: paper["title"] for paper in matrix["papers"]}
    gaps = []
    for gap in db.scalars(select(Gap).where(Gap.project_id == project.id).order_by(Gap.priority.desc(), Gap.created_at)):
        supporting = [sid for sid in gap.supporting_paper_ids or [] if sid in eligible_ids]
        future = [dict(item, title=titles.get(item["source_id"])) for item in gap.future_research or []
                  if item.get("source_id") in eligible_ids]
        omitted = len(gap.supporting_paper_ids or []) - len(supporting) + len(gap.future_research or []) - len(future)
        gaps.append({
            "gap_id": str(gap.id), "type": gap.type, "statement": gap.statement, "priority": gap.priority,
            "selected": gap.selected, "matrix_cell_ids": list(gap.matrix_cell_ids or []),
            "supporting_papers": [{"source_id": sid, "title": titles.get(sid)} for sid in supporting],
            "future_research": future, "omitted_ineligible_sources": omitted,
        })
    return {
        "schema_version": matrix["schema_version"], "peer_reviewed_only": project.peer_reviewed_only,
        "themes": themes,
        "agreements": comparison["agreements"], "conflicts": comparison["conflicts"],
        "inconsistent": comparison["inconsistent"], "unstructured_findings": comparison["unstructured_findings"],
        "gaps": gaps, "excluded": excluded,
    }


def _cite(item: dict) -> str:
    page = f", p. {item['page']}" if item.get("page") else ""
    return f"[source {item['source_id']}{page}] \u201c{item['quote']}\u201d"


def markdown(summary: dict, title: str) -> str:
    lines = [f"# Literature summary: {title}", "",
             "Built only from human-verified extractions of verified, non-retracted sources. Nothing here is generated text.", ""]
    lines += ["## Key findings by theme", ""]
    for theme in summary["themes"]:
        lines += [f"### {theme['label']}", ""]
        lines += [f"- {f['statement']} \u2014 {f['title']} {_cite(f)}" for f in theme["findings"]] + [""]
    for heading, key in (("Agreements", "agreements"), ("Conflicts", "conflicts"), ("Inconsistent findings", "inconsistent")):
        lines += [f"## {heading}", ""]
        for group in summary[key]:
            note = f" ({group['population_note']})" if group.get("population_note") else ""
            lines.append(f"- {group['exposure']} \u2192 {group['outcome']}{note}")
            lines += [f"  - {f['title']}: {f['direction'] or 'no effect'} {_cite(f)}" for f in group["findings"]]
        lines.append("")
    lines += ["## Gaps", ""]
    for gap in summary["gaps"]:
        lines.append(f"- ({gap['type']}) {gap['statement']}")
        lines += [f"  - supported by {p['title']} (source {p['source_id']})" for p in gap["supporting_papers"]]
    lines += ["", "## Sources excluded from this summary", ""]
    lines += [f"- {e['title']} (source {e['source_id']}): {e['reason_text']}" for e in summary["excluded"]]
    return "\n".join(lines) + "\n"
