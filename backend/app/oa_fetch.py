"""Open-access full text for a source: DOI → OA location sources → licence check → stored PDF (plan M2.7.1, M2.7.6).

Spec section 9: "store/process full text only where licensing permits; keep links and metadata
otherwise." `fetch_open_access` asks each open-access source in turn for its best OA copy of the
source's DOI: Unpaywall, then OpenAlex, then CORE (the caller passes them in that order as
`OaSource`s). It records the outcome on `Source.fulltext_access`:

  - `stored`        a source's licence is on the allow-list and its PDF was downloaded and stored
                    write-once; `Source.fulltext_path` holds its object-store key.
  - `link_only`     an OA copy exists, but its licence is unknown or not on the allow-list
                    (e.g. "implied-oa", "publisher-specific-oa"): the link and licence are kept, no file.
  - `no_pdf`        the licence allows storing, but the source has a landing page and no PDF link.
  - `no_oa`         the sources that answered have no open-access copy.
  - `lookup_failed` no copy was found and at least one source failed to answer.

The first source whose copy has a permitted licence and a PDF link supplies the file; later sources
are then not consulted. When nothing is stored, the first copy found (in source order) gives the
recorded link and licence. A source that fails to answer is recorded (its exception class name only:
messages can carry URLs or response text) and the next source is consulted. A disabled source is
recorded as `not_consulted` and never called.

An unknown licence is never treated as permissive. The allow-list is a setting
(`FULLTEXT_STORE_LICENCES`), matched on the licence strings the sources report.
Every download goes through the caller's `fetch_pdf` (the SSRF-guarded fetch in `safe_fetch`).

Failures are loud: an unknown DOI with no source able to answer, a download that is refused or
fails, or a stored file that differs from the one now offered raise `OaFetchError` (or the
fetch error) and nothing is recorded as fetched.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol, Sequence

from sqlalchemy.orm import Session

from app import audit
from app.connectors.base import ConnectorError
from app.connectors.unpaywall import OaLocation
from app.models import Project, Source, utcnow
from app.object_storage import ObjectConflict, ObjectStore, fulltext_key

STORED = "stored"
LINK_ONLY = "link_only"
NO_PDF = "no_pdf"
NO_OA = "no_oa"
LOOKUP_FAILED = "lookup_failed"

# Per-source outcomes, recorded in `fulltext_access["sources"]`.
STORABLE = "storable"  # licence permitted and a PDF link: this copy is downloaded
ERROR = "error"
NOT_CONSULTED = "not_consulted"

PdfFetcher = Callable[[str], tuple[bytes, str]]


class OaFetchError(Exception):
    """The full text can't be fetched for a reason a retry won't fix."""


class LocationLookup(Protocol):
    """What `fetch_open_access` needs from a source (see `UnpaywallConnector.get_oa_location`)."""

    def get_oa_location(self, doi: str) -> OaLocation | None: ...


@dataclass(frozen=True)
class OaSource:
    """One open-access location source, in consult order. `lookup` None means the source is disabled."""

    name: str
    lookup: LocationLookup | None


def licence_permits_storage(licence: str | None, allowed: tuple[str, ...] | list[str]) -> bool:
    if not licence:
        return False
    return licence.strip().lower() in {a.strip().lower() for a in allowed}


def _record_location(access: dict, source: Source, name: str, location: OaLocation) -> None:
    access.update(
        via=name, licence=location.license, version=location.version, host_type=location.host_type,
        pdf_url=location.pdf_url, landing_page_url=location.landing_page_url,
    )
    source.oa_url = location.pdf_url or location.landing_page_url or source.oa_url


def fetch_open_access(
    db: Session,
    *,
    project: Project,
    source: Source,
    sources: Sequence[OaSource],
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
    checked: list[dict] = []
    first_copy: tuple[str, OaLocation, str] | None = None  # the first OA copy found, with its outcome
    winner: tuple[str, OaLocation, str] | None = None  # the storable copy: (source, location, pdf_url)
    for index, entry in enumerate(sources):
        if entry.lookup is None:
            checked.append({"source": entry.name, "outcome": NOT_CONSULTED, "reason": "disabled"})
            continue
        try:
            location = entry.lookup.get_oa_location(source.doi)
        except ConnectorError as exc:
            checked.append({"source": entry.name, "outcome": ERROR, "error": type(exc).__name__})
            continue
        if location is None:
            checked.append({"source": entry.name, "outcome": NO_OA})
            continue
        pdf_url = location.pdf_url
        if not licence_permits_storage(location.license, allowed_licences):
            outcome = LINK_ONLY
        elif not pdf_url:
            outcome = NO_PDF
        else:
            outcome = STORABLE
        checked.append({"source": entry.name, "outcome": outcome})
        if first_copy is None:
            first_copy = (entry.name, location, outcome)
        if outcome == STORABLE and pdf_url:
            winner = (entry.name, location, pdf_url)
            checked.extend({"source": later.name, "outcome": NOT_CONSULTED, "reason": "not_needed"} for later in sources[index + 1 :])
            break
    access: dict = {"via": None, "checked_at": utcnow().isoformat(), "sources": checked}
    if winner is not None:
        name, location, pdf_url = winner
        _record_location(access, source, name, location)
        data, final_url = fetch_pdf(pdf_url)
        before_write()
        key = fulltext_key(project.id, source.id, "pdf")
        try:
            stored = store.put(key, data)
        except ObjectConflict as exc:
            raise OaFetchError("A different file is already stored for this source; it was not replaced") from exc
        source.fulltext_path = stored.key
        access.update(status=STORED, fetched_url=final_url, sha256=stored.sha256, size=stored.size)
    elif first_copy is not None:
        name, location, outcome = first_copy
        _record_location(access, source, name, location)
        access["status"] = outcome  # LINK_ONLY or NO_PDF
    else:
        failed = any(c["outcome"] == ERROR for c in checked)
        access["status"] = LOOKUP_FAILED if failed else NO_OA
    source.fulltext_access = access
    audit.record(
        db, actor=requested_by, action="source.fulltext_checked", project_id=project.id,
        payload={
            "source_id": str(source.id), "via": access.get("via"), "sources": checked,
            **{k: access.get(k) for k in ("status", "licence", "version", "host_type", "sha256", "size")},
        },
    )
    return access
