"""Check a stored source against scholarly services and record the outcome (plan M1.10.2).

`verify_source` runs `citation_verifier.verify_citation` on the source's own title, authors, year
and DOI. This is the **only automated path** to `metadata_verified` (the other is a person's
`/verify`); a static test keeps it that way.

* Whatever the verdict, the check is stored on the source (`verification`: verdict, reasons,
  services consulted, matched record id - no source text) and audited as
  `source.verification_checked` with the same facts.
* Only a `verified` verdict sets `metadata_verified`, with `verification_method = "automatic"`,
  the time, and the actor `agent:citation-verifier`. It never replaces a person's verification.
* An automatic verification is **not a person's review**: `Source.is_automated` stays true for
  retrieved sources, so prompts still say it was retrieved automatically and ranking is unchanged.
* A later check that no longer matches (`mismatch`) **revokes** an automatic verification (audited
  `source.verification_revoked`). `not_found`/`unavailable` change nothing - a service not knowing a
  work, or being down, is no reason to take verification away. A person's verification is never revoked.
* A `retracted` flag from a service is added to the source's `quality_flags`.
* This confirms the metadata matches a scholarly record. Where the source was itself retrieved from
  one of the consulted services, that service agreeing with itself adds little; the verdict lists
  which services answered so a person can judge.
"""

from __future__ import annotations

from typing import Sequence

from sqlalchemy.orm import Session

from app import audit
from app.citation_verifier import Reference, VerificationResult, Verdict, verify_citation
from app.connectors.base import Connector
from app.models import Project, Source, utcnow

AGENT_VERIFIER = "agent:citation-verifier"


def verify_source(db: Session, *, project: Project, source: Source, connectors: Sequence[Connector], requested_by: str) -> VerificationResult:
    reference = Reference(
        title=source.title,
        authors=tuple(str(a) for a in (source.authors or [])),
        year=source.year,
        doi=source.doi,
    )
    result = verify_citation(reference, connectors)
    summary = result.summary()
    now = utcnow()
    source.verification = {**summary, "checked_at": now.isoformat()}

    if result.verdict is Verdict.verified and not source.metadata_verified:
        source.metadata_verified = True
        source.verification_method = "automatic"
        source.verified_at = now
        source.verified_by = AGENT_VERIFIER
        audit.record(
            db, actor=AGENT_VERIFIER, action="source.verified", project_id=project.id,
            payload={"source_id": str(source.id), "method": "automatic", "matched": summary["matched"], "requested_by": requested_by},
        )
    elif result.verdict is Verdict.mismatch and source.metadata_verified and source.verification_method == "automatic":
        source.metadata_verified = False
        source.verification_method = source.verified_at = source.verified_by = None
        audit.record(
            db, actor=AGENT_VERIFIER, action="source.verification_revoked", project_id=project.id,
            payload={"source_id": str(source.id), "reasons": summary["reasons"], "requested_by": requested_by},
        )
    if "retracted" in result.flags and "retracted" not in (source.quality_flags or []):
        source.quality_flags = [*(source.quality_flags or []), "retracted"]

    audit.record(
        db, actor=AGENT_VERIFIER, action="source.verification_checked", project_id=project.id,
        payload={"source_id": str(source.id), "requested_by": requested_by, **summary},
    )
    project.touch()
    return result
