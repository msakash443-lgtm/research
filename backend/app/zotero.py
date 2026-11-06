"""Zotero library sync (M1.11.2): pull items into the project, push verified sources out.

Two-way sync over the official Zotero API (https://www.zotero.org/support/developer/api), using
the shared connector HTTP client (retries, rate gate, circuit breaker). The v1 reconciliation
rule (Decisions log 2026-10-10): **skip what already exists, adopt by DOI, never overwrite, never
delete.**

* A pull is bulk retrieval, so its task type needs gate G2, like database searches. Items arrive
  unverified with `origin=retrieved` (plan rule 18/rule 23: external content is data, not trust).
  `ingest_key = "zotero:<item key>"` makes a retried run a no-op.
* A push sends **verified sources only** (plan rule 24, same as the BibTeX/RIS export). The Zotero
  item key is stored in `source_ids["zotero"]`; on a re-run an item that was already created is
  found again by DOI and *adopted* rather than duplicated. Nothing here updates or deletes an
  existing Zotero item.

`ZoteroError` is a domain problem (bad configuration, an unusable reply); connector transport and
HTTP problems surface as `ConnectorError`. Both fail the task loudly — never a placeholder.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.config import Settings, get_settings
from app.connectors.base import ConnectorError
from app.connectors.http import ConnectorHttpClient, HttpPolicy
from app.doi import normalize_doi
from app.models import SOURCE_ORIGIN_RETRIEVED, Project, Source
from app.refmanager import _clean, _year

BASE_URL = "https://api.zotero.org"
AGENT_ZOTERO_SYNC = "agent:zotero-sync"
MAX_ITEMS = 2000
PAGE_SIZE = 100
# Zotero's API-key pool is shared and heavily used; keep a polite floor between requests.
_MIN_INTERVAL_SECONDS = 0.25

# Zotero item type -> this app's source_type (unknown types land as "other", never dropped).
ZOTERO_TO_SOURCE_TYPE = {
    "journalArticle": "article", "book": "book", "bookSection": "chapter", "conferencePaper": "conference",
    "thesis": "thesis", "report": "report", "preprint": "preprint", "webpage": "other",
}
# This app's source_type -> Zotero item type ("other" -> document, a Zotero catch-all).
SOURCE_TYPE_TO_ZOTERO = {
    "article": "journalArticle", "book": "book", "chapter": "bookSection", "conference": "conferencePaper",
    "thesis": "thesis", "report": "report", "preprint": "preprint", "other": "document",
}
# Where a source's venue lands depends on the Zotero item type it becomes.
_VENUE_FIELD = {
    "journalArticle": "publicationTitle", "bookSection": "bookTitle",
    "conferencePaper": "proceedingsTitle", "book": "publisher",
}


class ZoteroError(Exception):
    """Configuration or reply problem a retry cannot fix."""


def zotero_configured(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    return bool(
        settings.zotero_sync_enabled
        and settings.zotero_api_key
        and settings.zotero_api_key.get_secret_value().strip()
        and (settings.zotero_user_id or "").strip()
    )


class ZoteroClient:
    """A thin, testable view over the Zotero REST API for one user library."""

    def __init__(self, api_key: str, user_id: str, *, http: ConnectorHttpClient | None = None,
                 policy: HttpPolicy | None = None) -> None:
        self.user_id = user_id.strip()
        self.http = http or ConnectorHttpClient(
            "zotero", BASE_URL, policy or HttpPolicy(min_interval_seconds=_MIN_INTERVAL_SECONDS),
            headers={"Authorization": f"Bearer {api_key}"},
        )

    def list_items(self, collection_key: str | None = None, max_items: int | None = None) -> list[dict]:
        """Page through the library (or one collection) in order, up to `max_items` (default MAX_ITEMS)."""
        max_items = MAX_ITEMS if max_items is None else max_items
        items: list[dict] = []
        start = 0
        while len(items) < max_items:
            params: dict[str, Any] = {"limit": min(PAGE_SIZE, max_items - len(items)), "start": start}
            if collection_key:
                params["collectionKey"] = collection_key
            page = self.http.get_json(f"/users/{self.user_id}/items", params=params)
            if not isinstance(page, list) or not page:
                break
            items.extend(page)
            if len(page) < params["limit"]:
                break
            start += len(page)
        return items[:max_items]

    def create_item(self, item: dict) -> str:
        """Create one item; Zotero answers 201 with an empty body and the new key in `Location`."""
        response = self.http.post(f"/users/{self.user_id}/items", item)
        key = (response.headers.get("Location") or "").rsplit("/", 1)[-1].strip()
        if not key:
            raise ZoteroError("Zotero created an item but did not report its key (missing Location header).")
        return key


def build_zotero_client(settings: Settings | None = None) -> ZoteroClient:
    """The configured client, or `ZoteroError` naming what is missing (fail loudly, never off silently)."""
    settings = settings or get_settings()
    if not zotero_configured(settings):
        missing = []
        if not settings.zotero_sync_enabled:
            missing.append("ZOTERO_SYNC_ENABLED")
        if not (settings.zotero_api_key and settings.zotero_api_key.get_secret_value().strip()):
            missing.append("ZOTERO_API_KEY")
        if not (settings.zotero_user_id or "").strip():
            missing.append("ZOTERO_USER_ID")
        raise ZoteroError("Zotero sync is not configured on this server: set " + ", ".join(missing) + ".")
    return ZoteroClient(settings.zotero_api_key.get_secret_value(), settings.zotero_user_id)


# --- mapping ---------------------------------------------------------------


def _doi(value: Any) -> str | None:
    if not value:
        return None
    try:
        return normalize_doi(str(value))
    except ValueError:
        return None  # a malformed DOI in someone's library is not a sync blocker


def _creators_from_zotero(creators: Any) -> list[str]:
    out: list[str] = []
    for creator in creators or []:
        if not isinstance(creator, dict):
            continue
        last = _clean(creator.get("lastName"))
        first = _clean(creator.get("firstName"))
        if last and first:
            out.append(f"{last}, {first}")
        else:
            name = _clean(creator.get("name")) or _clean(last)
            if name:
                out.append(name)  # organization or undivided name
    return out


def _creator_to_zotero(author: str) -> dict:
    """`Last, First` -> split creator; anything else is an undivided name (e.g. an organization)."""
    text = _clean(author) or ""
    if "," in text:
        last, first = (part.strip() for part in text.split(",", 1))
        if last and first:
            return {"creatorType": "author", "lastName": last, "firstName": first}
    return {"creatorType": "author", "name": text}


def zotero_item_to_record(item: dict) -> dict | None:
    """One Zotero item as a source record; None when it has no usable title (reported, never invented)."""
    title = _clean(item.get("title"))
    if not title:
        return None
    doi = _doi(item.get("DOI") or item.get("doi"))
    url = _clean(item.get("URL") or item.get("url"))
    venue = _clean(item.get("publicationTitle") or item.get("bookTitle") or item.get("proceedingsTitle"))
    abstract = _clean(item.get("abstractNote"))
    return {
        "title": title,
        "authors": _creators_from_zotero(item.get("creators")) or None,
        "year": _year(str(item.get("date") or "")),
        "doi": doi,
        "url": url,
        "venue": venue,
        "abstract": abstract,
        "source_type": ZOTERO_TO_SOURCE_TYPE.get(str(item.get("itemType") or ""), "other"),
    }


def source_to_zotero_item(source: Source) -> dict:
    """A project source as a Zotero item body (only fields Zotero accepts on create)."""
    item_type = SOURCE_TYPE_TO_ZOTERO.get(source.source_type or "article", "document")
    item: dict[str, Any] = {"itemType": item_type, "title": source.title}
    if source.authors:
        item["creators"] = [_creator_to_zotero(a) for a in source.authors if _clean(a)]
    if source.year:
        item["date"] = str(source.year)
    if source.doi:
        item["DOI"] = source.doi
    if source.url:
        item["URL"] = source.url
    if source.venue and item_type in _VENUE_FIELD:
        item[_VENUE_FIELD[item_type]] = source.venue
    if source.abstract:
        item["abstractNote"] = source.abstract
    return item


def _stored_zotero_key(source_ids: dict | None) -> str | None:
    value = (source_ids or {}).get("zotero") if isinstance(source_ids, dict) else None
    return value if isinstance(value, str) and value.strip() else None


def _project_dois(db: Session, project: Project) -> set[str]:
    rows = db.scalars(select(Source.doi).where(
        Source.project_id == project.id, Source.doi.is_not(None), Source.merged_into.is_(None))).all()
    return {row for row in rows if row}


# --- sync ------------------------------------------------------------------


def sync_pull(
    db: Session,
    project: Project,
    client: ZoteroClient,
    *,
    collection_key: str | None = None,
    before_write: Callable[[], None] | None = None,
) -> dict:
    """Import the library's items as unverified sources. Safe to re-run: `ingest_key` stops
    duplicates. Returns the counts for the audit payload."""
    items = client.list_items(collection_key)
    known_dois = _project_dois(db, project)
    existing_keys = {
        row for row in db.scalars(select(Source.ingest_key).where(Source.project_id == project.id)).all() if row
    }
    added = skipped_existing = skipped_duplicate_doi = skipped_no_title = 0
    for item in items:
        key = str(item.get("key") or "")
        if not key:
            skipped_no_title += 1  # an item without a key can't be tracked, so it's unusable here
            continue
        ingest_key = f"zotero:{key}"
        if ingest_key in existing_keys:
            skipped_existing += 1
            continue
        record = zotero_item_to_record(item)
        if record is None:
            skipped_no_title += 1
            continue
        if record["doi"] and record["doi"] in known_dois:
            skipped_duplicate_doi += 1
            continue
        if before_write is not None:
            before_write()
        db.add(Source(
            project_id=project.id,
            title=record["title"][:500],
            doi=record["doi"],
            url=record["url"],
            authors=record["authors"],
            year=record["year"],
            source_type=record["source_type"],
            venue=record["venue"],
            abstract=record["abstract"],
            origin=SOURCE_ORIGIN_RETRIEVED,
            created_by=AGENT_ZOTERO_SYNC,
            ingest_key=ingest_key,
            source_ids={"zotero": key},
        ))
        if record["doi"]:
            known_dois.add(record["doi"])
        existing_keys.add(ingest_key)
        added += 1
    audit.record(
        db, actor=AGENT_ZOTERO_SYNC, action="zotero.pulled", project_id=project.id,
        payload={
            "library": f"user:{client.user_id}", "collection": collection_key,
            "total": len(items), "added": added, "skipped_existing": skipped_existing,
            "skipped_duplicate_doi": skipped_duplicate_doi, "skipped_no_title": skipped_no_title,
        },
    )
    if added:
        project.touch()
    return {
        "total": len(items), "added": added, "skipped_existing": skipped_existing,
        "skipped_duplicate_doi": skipped_duplicate_doi, "skipped_no_title": skipped_no_title,
    }


def sync_push(
    db: Session,
    project: Project,
    client: ZoteroClient,
    *,
    collection_key: str | None = None,
    before_write: Callable[[], None] | None = None,
) -> dict:
    """Create Zotero items for the project's verified sources. Safe to re-run: a source that
    already has a key is skipped, and an item a crashed earlier attempt already created is
    adopted by DOI instead of duplicated. Returns the counts for the audit payload."""
    sources = db.scalars(select(Source).where(
        Source.project_id == project.id, Source.merged_into.is_(None)).order_by(Source.created_at, Source.id)).all()
    verified = [s for s in sources if s.metadata_verified]
    # What is already in the target library/collection, by DOI, so a retry can adopt instead of duplicate.
    existing_by_doi: dict[str, str] = {}
    for item in client.list_items(collection_key):
        doi = _doi(item.get("DOI") or item.get("doi"))
        key = str(item.get("key") or "")
        if doi and key and doi not in existing_by_doi:
            existing_by_doi[doi] = key
    created = adopted = skipped_existing = 0
    for source in verified:
        if _stored_zotero_key(source.source_ids):
            skipped_existing += 1
            continue
        found = existing_by_doi.get((source.doi or "").lower())
        if found:
            ids = dict(source.source_ids) if isinstance(source.source_ids, dict) else {}
            ids["zotero"] = found
            source.source_ids = ids
            adopted += 1
            continue
        if before_write is not None:
            before_write()
        key = client.create_item(source_to_zotero_item(source))
        ids = dict(source.source_ids) if isinstance(source.source_ids, dict) else {}
        ids["zotero"] = key
        source.source_ids = ids
        if source.doi:
            existing_by_doi[source.doi.lower()] = key  # this run's creates are visible to later items
        created += 1
    audit.record(
        db, actor=AGENT_ZOTERO_SYNC, action="zotero.pushed", project_id=project.id,
        payload={
            "library": f"user:{client.user_id}", "collection": collection_key,
            "total_verified": len(verified), "created": created, "adopted": adopted,
            "skipped_existing": skipped_existing, "unverified_skipped": len(sources) - len(verified),
        },
    )
    if created or adopted:
        project.touch()
    return {
        "total_verified": len(verified), "created": created, "adopted": adopted,
        "skipped_existing": skipped_existing, "unverified_skipped": len(sources) - len(verified),
    }
