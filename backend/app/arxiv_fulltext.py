"""Licence-gated arXiv PDF retrieval and storage."""

from __future__ import annotations

import re
from typing import Callable, Protocol

from sqlalchemy.orm import Session

from app import audit
from app.connectors.arxiv import ArxivLicense, normalise_id
from app.connectors.base import ConnectorError
from app.models import SOURCE_ORIGIN_RETRIEVED, Project, Source, utcnow
from app.object_storage import ObjectConflict, ObjectStore, fulltext_key
from app.oa_fetch import LINK_ONLY, STORED, OaFetchError, licence_permits_storage

ARXIV_FULLTEXT_FETCH = "arxiv_fulltext_fetch"
_CC_LICENSE = re.compile(
    r"^https?://creativecommons\.org/licenses/(by(?:-sa|-nc(?:-sa|-nd)?|-nd)?)/(1\.0|2\.0|2\.5|3\.0|4\.0)/?$",
    re.IGNORECASE,
)
_CC0_LICENSE = re.compile(r"^https?://creativecommons\.org/publicdomain/zero/1\.0/?$", re.IGNORECASE)


class ArxivLicenseLookup(Protocol):
    def get_license(self, external_id: str) -> ArxivLicense | None: ...


PdfFetcher = Callable[[str], tuple[bytes, str]]


def _allowlist_name(uri: str | None) -> str | None:
    if not uri:
        return None
    if _CC0_LICENSE.fullmatch(uri.strip()):
        return "cc0"
    match = _CC_LICENSE.fullmatch(uri.strip())
    if match is None:
        return None
    return "cc-" + match.group(1).lower()


def fetch_arxiv_fulltext(
    db: Session,
    *,
    project: Project,
    source: Source,
    arxiv: ArxivLicenseLookup,
    fetch_pdf: PdfFetcher,
    store: ObjectStore,
    allowed_licences: tuple[str, ...] | list[str],
    requested_by: str,
    before_write: Callable[[], None] = lambda: None,
) -> dict:
    """Record the current arXiv licence and store its version-pinned PDF only when permitted."""
    if source.fulltext_path:
        raise OaFetchError("This source already has stored full text")
    if source.origin != SOURCE_ORIGIN_RETRIEVED:
        raise OaFetchError("Only a retrieved arXiv source can be fetched")
    source_ids = source.source_ids if isinstance(source.source_ids, dict) else {}
    raw_id = source_ids.get("arxiv")
    if not isinstance(raw_id, str) or not raw_id:
        raise OaFetchError("This source has no arXiv id to look up")
    try:
        ident = normalise_id(raw_id)
    except ConnectorError as exc:
        raise OaFetchError("This source has an invalid arXiv id") from exc

    license_record = arxiv.get_license(ident)
    license_uri = license_record.uri if license_record else None
    version = license_record.version if license_record else None
    allowed_name = _allowlist_name(license_uri)
    pdf_url = f"https://arxiv.org/pdf/{ident}{version}" if version else None
    access = {
        "via": "arxiv",
        "checked_at": utcnow().isoformat(),
        "licence": allowed_name,
        "license_uri": license_uri,
        "version": version,
        "pdf_url": pdf_url,
        "status": LINK_ONLY,
    }
    if pdf_url:
        source.oa_url = pdf_url

    if allowed_name and licence_permits_storage(allowed_name, allowed_licences):
        data, final_url = fetch_pdf(pdf_url)
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
        db,
        actor=requested_by,
        action="source.fulltext_checked",
        project_id=project.id,
        payload={
            "source_id": str(source.id),
            "via": "arxiv",
            "status": access["status"],
            "licence": allowed_name,
            "version": version,
            "sha256": access.get("sha256"),
            "size": access.get("size"),
        },
    )
    return access
