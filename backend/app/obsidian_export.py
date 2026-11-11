"""Build an Obsidian vault (a .zip of Markdown notes) from one project (plan X.34.1).

Verified only (the user's choice, and M1.10.3: unverified references can't be exported):
  - a source is exported only if its metadata is verified and it hasn't been merged away;
  - a research run is exported only if it completed and every [S#] it cites points to a source
    that is *still* verified and exported. Anything else is left out and counted, never softened.

Text from outside (titles, abstracts, excerpts, model answers) is untrusted (rule 23). Obsidian
plugins run code from notes (Dataview `dataview`/`dataviewjs` blocks and `= …` inline queries,
Templater `<% … %>` tags) and render HTML and `![[…]]`/`![](…)` embeds, so all of that is made
inert: abstracts sit in a plain `text` fence; answers keep their Markdown but lose fence languages,
HTML, Templater tags, inline queries and embeds.

Frontmatter is written by hand: every value is JSON-encoded, and a JSON scalar or list is valid YAML.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

# Reused, not re-implemented: the same parser and rule that block unverified citations at run time.
from app.agent.executor import _CITATION_GROUP, _cited_indices, _citation_problem
from app.models import ContextKind, Project, ResearchContextItem, ResearchRun, ResearchRunStatus, Source
from app.untrusted_text import _strip_invisible, clean_untrusted

MAX_NAME_CHARS = 120
MAX_TITLE_CHARS = 300
MAX_ABSTRACT_CHARS = 20_000

# Illegal in Windows file names, or breaks an Obsidian link ([[name#heading|alias^block]]).
_NAME_UNSAFE = re.compile(r'[\\/:*?"<>|#^\[\]\x00-\x1f]')
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_FENCE = re.compile(r"^(\s{0,3})(`{3,}|~{3,})(.*)$")
_INLINE_QUERY = re.compile(r"(?<!\\)`(?=\s*\$?=)")  # Dataview inline (`= …`) and inline JS (`$= …`)


@dataclass
class ExportStats:
    sources: int = 0
    skipped_sources: int = 0
    runs: int = 0
    skipped_runs: int = 0
    context_items: int = 0
    skipped_run_reasons: dict[str, int] = field(default_factory=dict)

    def as_payload(self) -> dict[str, Any]:
        return {
            "format": "obsidian",
            "sources": self.sources,
            "skipped_sources": self.skipped_sources,
            "runs": self.runs,
            "skipped_runs": self.skipped_runs,
            "skipped_run_reasons": dict(sorted(self.skipped_run_reasons.items())),
            "context_items": self.context_items,
        }


# --- making text safe ------------------------------------------------------------------------


def safe_name(text: str | None, fallback: str = "Untitled") -> str:
    """A file/folder name that works on Windows, macOS and Linux and inside an Obsidian [[link]]."""
    name = _NAME_UNSAFE.sub(" ", _strip_invisible(text or "")[0])
    name = re.sub(r"\s+", " ", name).strip(" .")[:MAX_NAME_CHARS].strip(" .")
    if not name:
        name = fallback
    if name.split(".")[0].upper() in _WINDOWS_RESERVED:
        name = f"_{name}"
    return name


def _neutralise_templater(text: str) -> str:
    return text.replace("<%", "< %")


def safe_inline(text: str) -> str:
    """Markdown text (outside code) with nothing that executes, embeds or renders as HTML."""
    text = _neutralise_templater(text)
    text = text.replace("<", "&lt;")
    text = text.replace("![", "\\![")
    return _INLINE_QUERY.sub("\\`", text)


def safe_markdown(text: str) -> str:
    """Untrusted multi-line Markdown kept readable but inert: fences lose their language (so no
    `dataview`/`dataviewjs` block runs), and everything outside fences goes through `safe_inline`."""
    text, _ = _strip_invisible(text)
    lines, fence = [], None
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        match = _FENCE.match(line)
        if fence is None and match:
            fence = match.group(2)
            lines.append(f"{match.group(1)}{fence}text")
        elif fence is not None and match and match.group(2).startswith(fence[0] * len(fence)) and not match.group(3).strip():
            fence = None
            lines.append(line)
        elif fence is not None:
            lines.append(_neutralise_templater(line))
        else:
            lines.append(safe_inline(line))
    if fence is not None:  # close an unterminated fence so it can't swallow the rest of the note
        lines.append(fence)
    return "\n".join(lines)


def fenced_text(text: str) -> str:
    """Untrusted text shown verbatim in a plain `text` code block that its content can't close."""
    text = _neutralise_templater(text)
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}text\n{text}\n{fence}"


def _clean_line(text: str | None, limit: int = MAX_TITLE_CHARS) -> str:
    return clean_untrusted(text, limit).text if text else ""


def _frontmatter(values: dict[str, Any]) -> str:
    rows = [f"{key}: {json.dumps(value, ensure_ascii=False, default=str)}" for key, value in values.items() if value is not None]
    return "---\n" + "\n".join(rows) + "\n---\n"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


# --- naming ----------------------------------------------------------------------------------


class _Names:
    """Unique note names per folder (case-insensitive, as on Windows/macOS)."""

    def __init__(self):
        self._taken: dict[str, set[str]] = {}

    def claim(self, folder: str, base: str) -> str:
        taken = self._taken.setdefault(folder, set())
        name, n = base, 2
        while name.casefold() in taken:
            suffix = f" ({n})"
            name = base[: MAX_NAME_CHARS - len(suffix)].rstrip(" .") + suffix
            n += 1
        taken.add(name.casefold())
        return name


def _first_author(authors: Any) -> str | None:
    if not authors:
        return None
    first = authors[0]
    if isinstance(first, dict):
        first = first.get("name") or first.get("family")
    if not first:
        return None
    first = str(first).strip()
    # "Surname, Given" or "Given Surname"
    return first.split(",")[0].strip() if "," in first else first.split()[-1] if first.split() else None


def _source_base_name(source: Source) -> str:
    lead = " ".join(part for part in (_first_author(source.authors), str(source.year) if source.year else None) if part)
    title = _clean_line(source.title, MAX_NAME_CHARS)
    return safe_name(f"{lead} - {title}" if lead else title)


# --- notes -----------------------------------------------------------------------------------


def _source_note(source: Source) -> str:
    authors = [_clean_line(str(a), 200) for a in (source.authors or [])]
    fm = _frontmatter(
        {
            "id": str(source.id),
            "title": _clean_line(source.title),
            "authors": authors or None,
            "year": source.year,
            "venue": _clean_line(source.venue) or None,
            "doi": source.doi,
            "url": source.url,
            "type": source.source_type,
            "origin": source.origin,
            "verified": True,
            "verification_method": source.verification_method or "human",
            "verified_at": _iso(source.verified_at),
            "tags": ["source"],
        }
    )
    body = [f"# {safe_inline(_clean_line(source.title))}", ""]
    meta = [
        ("Authors", "; ".join(authors)),
        ("Year", str(source.year) if source.year else ""),
        ("Venue", _clean_line(source.venue)),
        ("DOI", f"https://doi.org/{source.doi}" if source.doi else ""),
        ("URL", source.url or ""),
        ("Verified", f"yes ({source.verification_method or 'human'})"),
    ]
    body += [f"- **{label}:** {safe_inline(value)}" for label, value in meta if value]
    if source.abstract:
        body += ["", "## Abstract", "", "> [!note] Retrieved text, shown verbatim; not checked by a person.", "", fenced_text(clean_untrusted(source.abstract, MAX_ABSTRACT_CHARS).text)]
    return fm + "\n" + "\n".join(body) + "\n"


def _run_note(run: ResearchRun, linked: dict[int, str]) -> str:
    def link(match: re.Match) -> str:
        indices = sorted(_cited_indices(match.group(0)))
        return ", ".join(f"[[Sources/{linked[i]}|S{i}]]" for i in indices)

    answer = _CITATION_GROUP.sub(link, safe_markdown(run.answer or ""))
    fm = _frontmatter(
        {
            "id": str(run.id),
            "question": _clean_line(run.question, 1000),
            "status": run.status.value,
            "model": run.provider_model,
            "prompt_version": run.prompt_version,
            "created_by": run.created_by,
            "created_at": _iso(run.created_at),
            "completed_at": _iso(run.completed_at),
            "tags": ["research-run"],
        }
    )
    body = [
        f"# {safe_inline(_clean_line(run.question, 1000))}",
        "",
        f"> [!info] AI-generated answer — model: {safe_inline(_clean_line(run.provider_model) or 'unknown')}; "
        f"prompt: {safe_inline(_clean_line(run.prompt_version) or 'unknown')}; completed {_iso(run.completed_at) or 'unknown'}.",
        "",
        answer,
        "",
        "## Cited sources",
        "",
        *(f"- S{i}: [[Sources/{name}]]" for i, name in sorted(linked.items())),
    ]
    return fm + "\n" + "\n".join(body) + "\n"


def _context_note(item: ResearchContextItem) -> str:
    fm = _frontmatter(
        {
            "id": str(item.id),
            "kind": item.kind.value,
            "created_by": item.created_by,
            "created_at": _iso(item.created_at),
            "tags": ["context", item.kind.value],
        }
    )
    body = [f"# {item.kind.value.capitalize()}", "", safe_markdown(item.content)]
    if item.rationale:
        body += ["", "## Rationale", "", safe_markdown(item.rationale)]
    return fm + "\n" + "\n".join(body) + "\n"


# --- selection -------------------------------------------------------------------------------


def _run_problem(run: ResearchRun, exported: dict[str, str]) -> tuple[str | None, dict[int, str]]:
    """Why a run can't be exported (None if it can), and its cited index -> source note name."""
    if run.status == ResearchRunStatus.completed and run.insufficient_evidence:
        return "insufficient_evidence", {}  # the model abstained: there is no answer to export
    if run.status != ResearchRunStatus.completed or not run.answer:
        return f"status_{run.status.value}", {}
    snapshot_sources = (run.input_snapshot or {}).get("sources") or []
    if _citation_problem(run.answer, snapshot_sources):
        return "unverified_citation", {}
    linked = {}
    for index in _cited_indices(run.answer):
        name = exported.get(str(snapshot_sources[index - 1].get("id")))
        if name is None:  # verified when the run ran, but no longer (or merged away) now
            return "source_no_longer_verified", {}
        linked[index] = name
    return None, linked


def build_vault(
    project: Project,
    sources: Iterable[Source],
    context_items: Iterable[ResearchContextItem],
    runs: Iterable[ResearchRun],
    exported_at: datetime,
) -> tuple[bytes, ExportStats]:
    stats, names = ExportStats(), _Names()
    files: dict[str, str] = {}

    exported: dict[str, str] = {}  # source id -> note name
    for source in sources:
        if not source.metadata_verified or source.merged_into is not None:
            stats.skipped_sources += 1
            continue
        name = names.claim("Sources", _source_base_name(source))
        exported[str(source.id)] = name
        files[f"Sources/{name}.md"] = _source_note(source)
        stats.sources += 1

    run_names = []
    for run in sorted(runs, key=lambda r: r.created_at or exported_at):
        problem, linked = _run_problem(run, exported)
        if problem:
            stats.skipped_runs += 1
            stats.skipped_run_reasons[problem] = stats.skipped_run_reasons.get(problem, 0) + 1
            continue
        date = (run.completed_at or run.created_at or exported_at).date().isoformat()
        name = names.claim("Runs", safe_name(f"{date} - {_clean_line(run.question, 80)}"))
        run_names.append(name)
        files[f"Runs/{name}.md"] = _run_note(run, linked)
        stats.runs += 1

    context_names = []
    for item in sorted(context_items, key=lambda c: c.created_at or exported_at):
        name = names.claim("Context", safe_name(f"{item.kind.value} - {_clean_line(item.content, 60)}"))
        context_names.append((item.kind, name))
        files[f"Context/{name}.md"] = _context_note(item)
        stats.context_items += 1

    files["_Index.md"] = _index_note(project, stats, exported_at, context_names, sorted(exported.values(), key=str.casefold), run_names)

    root = safe_name(project.title, "Project")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in files.items():
            archive.writestr(f"{root}/{path}", content)
    return buffer.getvalue(), stats


def _index_note(project, stats, exported_at, context_names, source_names, run_names) -> str:
    fm = _frontmatter({"project_id": str(project.id), "stage": getattr(project.stage, "value", project.stage), "exported_at": _iso(exported_at), "tags": ["project-index"]})
    body = [
        f"# {safe_inline(_clean_line(project.title))}",
        "",
        f"> [!warning] Verified-only export ({_iso(exported_at)}). Left out: {stats.skipped_sources} unverified or merged source(s) "
        f"and {stats.skipped_runs} research run(s) that did not complete, found the evidence insufficient, or cite a source that isn't verified now.",
        "",
    ]
    if project.description:
        body += [safe_markdown(project.description), ""]
    order = list(ContextKind)
    context_links = [f"- [[Context/{name}]]" for _, name in sorted(context_names, key=lambda kn: order.index(kn[0]))]
    body += ["## Context", ""] + (context_links or ["- none"])
    body += ["", "## Research runs", ""] + ([f"- [[Runs/{name}]]" for name in run_names] or ["- none"])
    body += ["", "## Sources", ""] + ([f"- [[Sources/{name}]]" for name in source_names] or ["- none"])
    return fm + "\n" + "\n".join(body) + "\n"
