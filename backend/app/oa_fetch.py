"""Open-access full text for a source: DOI → Unpaywall → licence check → stored PDF (plan M2.7.1).

Spec section 9: "store/process full text only where licensing permits; keep links and metadata
otherwise." `fetch_open_access` asks Unpaywall for the best OA copy of the source's DOI and records
the outcome on `Source.fulltext_access`:

  - `stored`     the licence is on the allow-list and the PDF was downloaded and stored write-once;
                 `Source.fulltext_path` holds its object-store key.
  - `link_only`  there is an OA copy, but its licence is unknown or not on the allow-list
                 (e.g. "implied-oa", "publisher-specific-oa"): the link and licence are kept, no file.
  - `no_pdf`     the licence allows storing, but Unpaywall has a landing page and no PDF link.
  - `no_oa`      Unpaywall knows the DOI and has no open-access copy.

An unknown licence is never treated as permissive. The allow-list is a setting
(`FULLTEXT_STORE_LICENCES`), matched on Unpaywall's licence strings.

Failures are loud: an unknown DOI, a download that is refused or fails, or a stored file that
differs from the one now offered raise `OaFetchError` (or the connector/fetch error) and nothing
is recorded as fetched.
"""

from __future__ import annotations

from typing import Callable, Protocol

from sqlalchemy.orm import Session

from app import audit
from app.connectors.unpaywall import OaLocation
from app.models import Project, Source, utcnow
from app.object_storage import ObjectConflict, ObjectStore, fulltext_key

STORED = "stored"
LINK_ONLY = "link_only"
NO_PDF = "no_pdf"
NO_OA = "no_oa"

PdfFetcher = Callable[[str], tuple[bytes, str]]


class OaFetchError(Exception):
    """The full text can't be fetched for a reason a retry won't fix."""


class LocationLookup(Protocol):
    """What `fetch_open_access` needs from Unpaywall (see `UnpaywallConnector.get_oa_location`)."""

    def get_oa_location(self, doi: str) -> OaLocation | None: ...


def licence_permits_storage(licence: str | None, allowed: tuple[str, ...] | list[str]) -> bool:
    if not licence:
        return False
    return licence.strip().lower() in {a.strip().lower() for a in allowed}


def fetch_open_access(
    db: Session,
    *,
    project: Project,
    source: Source,
    unpaywall: LocationLookup,
    fetch_pdf: PdfFetcher,
    store: ObjectStore,
    allowed_licences: tuple[str, ...] | list[str],
    requested_by: str,
    before_write: Callable[[], None] = lambda: None,
) -> dict:
    """Look up, licence-check and (if allowed) store the source's OA PDF. Returns `fulltext_access`.

    The caller commits. `before_write` runs just before the object store is written (a task uses it
    to check it still owns the work).
    """
    if source.fulltext_path:
        raise OaFetchError("This source already has stored full text")
    if not source.doi:
        raise OaFetchError("This source has no DOI to look up")
    location = unpaywall.get_oa_location(source.doi)
    access: dict = {"via": "unpaywall", "checked_at": utcnow().isoformat()}
    if location is None:
        access["status"] = NO_OA
    else:
        access.update(
            licence=location.license, version=location.version, host_type=location.host_type,
            pdf_url=location.pdf_url, landing_page_url=location.landing_page_url,
        )
        source.oa_url = location.pdf_url or location.landing_page_url or source.oa_url
        if not licence_permits_storage(location.license, allowed_licences):
            access["status"] = LINK_ONLY
        elif not location.pdf_url:
            access["status"] = NO_PDF
        else:
            data, final_url = fetch_pdf(location.pdf_url)
            before_write()
            key = fulltext_key(project.id, source.id, "pdf")
            try:
                stored = store.put(key, data)
            except ObjectConflict as exc:
                raise OaFetchError("A different file is already stored for this source; it was not replaced") from exc
            source.fulltext_path = stored.key
            access.update(status=STORED, fetched_url=final_url, sha256=stored.sha256, size=stored.size)
    source.fulltext_access = access
    audit.record(
        db, actor=requested_by, action="source.fulltext_checked", project_id=project.id,
        payload={"source_id": str(source.id), **{k: access.get(k) for k in ("status", "licence", "version", "host_type", "sha256", "size")}},
    )
    return access
