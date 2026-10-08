"""Snowballing / citation chasing (spec 5.2.3, plan M1.9).

Backward (the references of a paper) and forward (the papers that cite it), starting from the
project's seed papers and/or chosen sources, repeated for N rounds: round 2 starts from what round 1
found, and so on.

Rules this module keeps:

* Only the `snowball_run` task handler calls `run_snowball`, and that task type needs gate G2
  (M1.8.1): citation chasing is bulk retrieval, so nothing runs until a person approved the strategy.
* Caps everywhere: rounds (1-3), works per paper per direction (`max_per_paper`, passed to the
  connector) and new works per run (`max_new`). Hitting any cap is recorded (`counts["capped"]`,
  `truncated_papers`), never hidden.
* A connector failure fails the whole run (plan rule 22): `SnowballFailed` after a `snowball.failed`
  audit event, and nothing else is saved. A start paper the service does not know is not a failure;
  it is listed as unresolved with the reason.
* What it finds is unverified third-party data: new `Source` rows are `origin="retrieved"`,
  `metadata_verified=False`, `created_by="agent:snowball"`, and carry `found_via` (which paper,
  which direction, which round) so the screening queue can show where each one came from (M1.9.2).
* A work already in the project (same DOI, else same title+year+first author with no conflicting
  DOI, else the same id at this connector) is not added again; it is counted as already present.
* Each source gets an `ingest_key` derived from the run and the work, so a retried run can't add it twice.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.connectors.base import Connector, ConnectorError, NotSupportedError, PaperRecord
from app.dedupe import doi_key, work_key
from app.models import SOURCE_ORIGIN_RETRIEVED, Project, SeedPaper, SnowballRun, Source

AGENT_SNOWBALL = "agent:snowball"
CONNECTORS = ("openalex", "semantic_scholar", "pubmed", "opencitations")  # services with both references and citations
DIRECTIONS = ("backward", "forward")
MAX_ROUNDS = 3
MAX_PER_PAPER = 200
MAX_NEW = 1000
MAX_STARTS = 50


class SnowballError(ValueError):
    """The run can't start: bad arguments, or no start paper could be found at the service."""


class SnowballFailed(RuntimeError):
    """The service failed during the run; nothing was saved."""


@dataclass(frozen=True)
class Start:
    """A paper to start from. `via` is what provenance shows (title plus the project's own id for it)."""

    via: dict[str, Any]
    doi: str | None = None
    external_id: str | None = None  # the paper's id at the connector, when already known


@dataclass(frozen=True)
class Found:
    record: PaperRecord
    direction: str
    round: int
    via: dict[str, Any]


@dataclass
class Walk:
    found: list[Found] = field(default_factory=list)
    starts: list[dict[str, Any]] = field(default_factory=list)
    rounds: list[dict[str, Any]] = field(default_factory=list)
    capped: bool = False


class KnownWorks:
    """What the project already holds, with the same matching rules as `dedupe` (different DOIs never match)."""

    def __init__(self, connector_name: str):
        self.connector_name = connector_name
        self.dois: set[str] = set()
        self.keys: dict[str, str | None] = {}  # work key -> DOI held under it (None if none)
        self.ids: set[str] = set()

    def add(self, item: Any, external_id: str | None = None) -> None:
        doi = doi_key(item.doi)
        if doi:
            self.dois.add(doi)
        key = work_key(item)
        if key is not None and (key not in self.keys or self.keys[key] is None):
            self.keys[key] = doi
        ids = getattr(item, "source_ids", None) or {}
        ext = external_id or (ids.get(self.connector_name) if isinstance(ids, dict) else None)
        if ext:
            self.ids.add(str(ext))

    def __contains__(self, record: PaperRecord) -> bool:
        if record.external_id in self.ids:
            return True
        doi = doi_key(record.doi)
        if doi and doi in self.dois:
            return True
        key = work_key(record)
        if key is None or key not in self.keys:
            return False
        held = self.keys[key]
        return not (doi and held and held != doi)


def _resolve(connector: Connector, start: Start) -> tuple[str | None, str | None]:
    """(id at the connector, None) or (None, why it could not be found)."""
    if start.external_id:
        return start.external_id, None
    if not start.doi:
        return None, "no DOI and no id at this service"
    lookup = getattr(connector, "get_by_doi", None)
    record = lookup(start.doi) if lookup else connector.get_by_id(start.doi)
    if record is None:
        return None, "the service has no record of this DOI"
    return record.external_id, None


def walk(
    connector: Connector,
    starts: list[Start],
    *,
    directions: tuple[str, ...],
    rounds: int,
    max_new: int,
    known: KnownWorks,
) -> Walk:
    """Chase citations. Network only, no database. Raises `ConnectorError` on a service failure."""
    result = Walk()
    frontier: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for start in starts:
        external_id, reason = _resolve(connector, start)
        result.starts.append({**start.via, "resolved": external_id is not None, **({"external_id": external_id} if external_id else {"reason": reason})})
        if external_id and external_id not in seen:
            seen.add(external_id)
            frontier.append((external_id, start.via))
    if not frontier:
        raise SnowballError("None of the start papers could be found at " + connector.name)

    for round_no in range(1, rounds + 1):
        stats = {"round": round_no, "papers_expanded": 0, "fetched": 0, "new": 0, "already_in_project": 0, "repeated": 0, "truncated_papers": 0}
        next_frontier: list[tuple[str, dict[str, Any]]] = []
        for external_id, via in frontier:
            if result.capped:
                break
            stats["papers_expanded"] += 1
            for direction in directions:
                records = connector.get_references(external_id) if direction == "backward" else connector.get_citations(external_id)
                if getattr(connector, "last_related_truncated", False):
                    stats["truncated_papers"] += 1
                for record in records:
                    stats["fetched"] += 1
                    if record.external_id in seen:
                        stats["repeated"] += 1
                        continue
                    seen.add(record.external_id)
                    if record in known:
                        stats["already_in_project"] += 1
                        continue
                    if len(result.found) >= max_new:
                        result.capped = True
                        break
                    known.add(record)
                    result.found.append(Found(record=record, direction=direction, round=round_no, via=via))
                    next_frontier.append((record.external_id, {"title": record.title, "external_id": record.external_id}))
                    stats["new"] += 1
                if result.capped:
                    break
        result.rounds.append(stats)
        frontier = next_frontier
        if not frontier or result.capped:
            break
    return result


def start_papers(db: Session, project: Project, connector_name: str, *, include_seeds: bool, source_ids: list[uuid.UUID]) -> list[Start]:
    """The project's seed papers and/or chosen (not merged) sources, as start points."""
    starts: list[Start] = []
    if include_seeds:
        for seed in db.scalars(select(SeedPaper).where(SeedPaper.project_id == project.id).order_by(SeedPaper.created_at, SeedPaper.id)):
            starts.append(Start(via={"seed_id": str(seed.id), "title": seed.title}, doi=seed.doi))
    if source_ids:
        rows = db.scalars(
            select(Source).where(Source.project_id == project.id, Source.id.in_(source_ids), Source.merged_into.is_(None))
        ).all()
        by_id = {s.id: s for s in rows}
        for sid in source_ids:
            source = by_id.get(sid)
            if source is None:
                raise SnowballError(f"Source {sid} is not in this project")
            ids = source.source_ids if isinstance(source.source_ids, dict) else {}
            starts.append(Start(via={"source_id": str(source.id), "title": source.title}, doi=source.doi, external_id=ids.get(connector_name)))
    if not starts:
        raise SnowballError("Nothing to start from: add seed papers or choose sources")
    if len(starts) > MAX_STARTS:
        raise SnowballError(f"At most {MAX_STARTS} start papers per run")
    return starts


def _ingest_key(run_id: uuid.UUID, record: PaperRecord) -> str:
    digest = hashlib.sha256(f"{record.connector}:{record.external_id}".encode()).hexdigest()[:24]
    return f"snow:{run_id}:{digest}"


def run_snowball(
    db: Session,
    *,
    project: Project,
    actor: str,
    connector: Connector,
    run_id: uuid.UUID,
    directions: list[str],
    rounds: int,
    max_per_paper: int,
    max_new: int,
    include_seeds: bool,
    source_ids: list[uuid.UUID],
    before_write: Callable[[Session], None] | None = None,
) -> SnowballRun:
    """Run one snowball and store it with the sources it found, in the caller's transaction."""
    if connector.name not in CONNECTORS:
        raise SnowballError(f"Snowballing needs one of: {', '.join(CONNECTORS)}")
    if not directions or any(d not in DIRECTIONS for d in directions):
        raise SnowballError("directions must be 'backward' and/or 'forward'")
    if not 1 <= rounds <= MAX_ROUNDS or not 1 <= max_per_paper <= MAX_PER_PAPER or not 1 <= max_new <= MAX_NEW:
        raise SnowballError("rounds, max_per_paper or max_new out of range")
    if hasattr(connector, "max_related"):
        connector.max_related = max_per_paper
    starts = start_papers(db, project, connector.name, include_seeds=include_seeds, source_ids=source_ids)

    known = KnownWorks(connector.name)
    for source in db.scalars(select(Source).where(Source.project_id == project.id, Source.merged_into.is_(None))):
        known.add(source)
    for start in starts:
        if start.external_id:
            known.ids.add(start.external_id)

    ordered = list(dict.fromkeys(directions))
    try:
        result = walk(connector, starts, directions=tuple(ordered), rounds=rounds, max_new=max_new, known=known)
    except NotSupportedError as exc:
        raise SnowballError(str(exc)) from exc
    except ConnectorError as exc:
        audit.record(
            db, actor=actor, action="snowball.failed", project_id=project.id,
            payload={"run_id": str(run_id), "connector": connector.name, "error": type(exc).__name__},
        )
        raise SnowballFailed(f"{connector.name} failed during snowballing: {exc}") from exc

    counts = {
        "rounds": result.rounds,
        "starts": len(result.starts),
        "unresolved_starts": sum(1 for s in result.starts if not s["resolved"]),
        "fetched": sum(r["fetched"] for r in result.rounds),
        "added": len(result.found),
        "already_in_project": sum(r["already_in_project"] for r in result.rounds),
        "truncated_papers": sum(r["truncated_papers"] for r in result.rounds),
        "capped": result.capped,
        "skipped_records": int(getattr(connector, "skipped_records", 0) or 0),
    }
    if before_write is not None:
        before_write(db)
    run = SnowballRun(
        id=run_id, project_id=project.id, connector=connector.name, directions=ordered, rounds=rounds,
        caps={"max_per_paper": max_per_paper, "max_new": max_new}, starts=result.starts, counts=counts,
        run_at=datetime.now(timezone.utc), created_by=actor,
    )
    db.add(run)
    for item in result.found:
        rec = item.record
        db.add(
            Source(
                project_id=project.id, title=rec.title[:500], doi=rec.doi, url=rec.url, authors=list(rec.authors) or None,
                year=rec.year, venue=rec.venue, abstract=rec.abstract, oa_url=rec.oa_url, source_ids=dict(rec.source_ids) or None,
                quality_flags=list(rec.quality_flags) or None, origin=SOURCE_ORIGIN_RETRIEVED, metadata_verified=False,
                created_by=AGENT_SNOWBALL, ingest_key=_ingest_key(run_id, rec),
                found_via={"method": "snowball", "run_id": str(run_id), "connector": connector.name,
                           "direction": item.direction, "round": item.round, "via": item.via},
            )
        )
    db.flush()
    audit.record(
        db, actor=actor, action="snowball.run", project_id=project.id,
        payload={"run_id": str(run_id), "connector": connector.name, "directions": ordered, "rounds": rounds,
                 "added": counts["added"], "fetched": counts["fetched"], "capped": counts["capped"], "verified": False},
    )
    if result.found:
        project.touch()
    return run


__all__ = [
    "AGENT_SNOWBALL", "CONNECTORS", "DIRECTIONS", "KnownWorks", "MAX_NEW", "MAX_PER_PAPER", "MAX_ROUNDS", "MAX_STARTS",
    "SnowballError", "SnowballFailed", "Start", "run_snowball", "start_papers", "walk",
]
