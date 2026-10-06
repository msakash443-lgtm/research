"""Merge duplicate stored sources into one (spec 5.2.2, plan M1.6.4).

A person picks sources that are the same work; `merge_sources` keeps one row (the *kept* source),
fills it from the others with the rules of `app.merge`, and hides the rest.

* **Nothing is deleted.** The others get `merged_into = kept.id`: they leave lists and prompts but
  stay in the table, so old run snapshots, audit events and `ingest_key`s (which stop a retried
  retrieval from re-adding the paper) still resolve.
* **Excerpts move to the kept source** with their content and hash untouched.
* **Never two different DOIs** (the same rule as `dedupe`/`merge`), and the sources must really
  form one duplicate group - this is not a way to glue unrelated papers together.
* **Verification is never promoted** (plan rule 24). The kept source stays verified only if it was
  verified *and* every field value that came from another source came from a verified one;
  otherwise the flag is cleared, because a person confirmed the old metadata, not the merged one.
  `source.merged` records when that happened.
* Merged ids, how each matched and every field decision go into the `source.merged` audit event.
"""

from __future__ import annotations

import re
import uuid
from typing import Sequence

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app import audit
from app.connectors.base import PaperRecord
from app.dedupe import group_duplicates
from app.merge import MergeError, merge_records
from app.models import Project, Source

MAX_MERGE = 20
_SYNTHETIC = re.compile(r"^src\d+$")


class SourceMergeError(ValueError):
    """The sources can't be merged (reason in the message)."""


def _record(index: int, source: Source) -> PaperRecord:
    try:
        return PaperRecord(
            connector=f"src{index}",
            external_id=str(source.id),
            title=source.title,
            doi=source.doi,
            authors=tuple(str(a) for a in (source.authors or [])),
            year=source.year,
            venue=source.venue,
            abstract=source.abstract,
            url=source.url,
            oa_url=source.oa_url,
            source_ids={str(k): str(v) for k, v in (source.source_ids or {}).items()},
            quality_flags=tuple(source.quality_flags or ()),
        )
    except ValidationError as exc:
        raise SourceMergeError(f"Source {source.id} has data that can't be merged: {exc.errors()[0]['msg']}") from exc


def merge_sources(
    db: Session, *, project: Project, source_ids: Sequence[uuid.UUID], actor: str, keep: uuid.UUID | None = None
) -> Source:
    ids = list(dict.fromkeys(source_ids))
    if not 2 <= len(ids) <= MAX_MERGE:
        raise SourceMergeError(f"Choose between 2 and {MAX_MERGE} different sources")
    if keep is not None and keep not in ids:
        raise SourceMergeError("The source to keep must be one of the sources being merged")
    found = {
        s.id: s
        for s in db.scalars(
            select(Source)
            .where(Source.project_id == project.id, Source.id.in_(ids), Source.merged_into.is_(None))
            .options(selectinload(Source.excerpts))
        )
    }
    if len(found) != len(ids):
        raise SourceMergeError("One or more sources were not found in this project (or were already merged)")

    if keep is not None:
        order = [found[keep]] + sorted((s for s in found.values() if s.id != keep), key=lambda s: (s.created_at, str(s.id)))
    else:  # verified first, then oldest
        order = sorted(found.values(), key=lambda s: (not s.metadata_verified, s.created_at, str(s.id)))
    anchor = order[0]

    records = [_record(i, s) for i, s in enumerate(order)]
    if len(group_duplicates(records)) != 1:
        raise SourceMergeError("These sources don't look like the same work (title, year, first author or DOI differ)")
    try:
        result = merge_records(records)
    except MergeError as exc:
        raise SourceMergeError(str(exc)) from exc

    verified = {f"src{i}:{s.id}": s.metadata_verified for i, s in enumerate(order)}
    keeps_verification = anchor.metadata_verified and all(verified.get(d.chosen_from, False) for d in result.decisions)

    merged = result.record
    anchor.title = merged.title
    anchor.doi = merged.doi
    anchor.authors = list(merged.authors) or None
    anchor.year = merged.year
    anchor.venue = merged.venue
    anchor.abstract = merged.abstract
    anchor.url = merged.url
    anchor.oa_url = merged.oa_url
    anchor.source_ids = {k: v for k, v in merged.source_ids.items() if not _SYNTHETIC.match(k)} or None
    anchor.quality_flags = list(merged.quality_flags) or None
    cleared = anchor.metadata_verified and not keeps_verification
    if cleared:
        anchor.metadata_verified = False
        anchor.verification_method = anchor.verified_at = anchor.verified_by = None
    if any(d.chosen_from != f"src0:{anchor.id}" for d in result.decisions):
        anchor.verification = None  # the old check described metadata that has now changed

    for other in order[1:]:
        for excerpt in list(other.excerpts):
            excerpt.source = anchor
        other.merged_into = anchor.id
    db.flush()

    payload = result.audit_payload()
    payload["kept"] = str(anchor.id)
    payload["merged"] = [str(s.id) for s in order[1:]]
    payload["verification_cleared"] = cleared
    audit.record(db, actor=actor, action="source.merged", project_id=project.id, payload=payload)
    return anchor
