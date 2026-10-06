"""Check that a reference really exists with the metadata it claims (spec 3.3, 7.3; plan M1.10.1).

`verify_citation(reference, connectors)` looks the reference up in scholarly services and returns a
`VerificationResult` whose verdict is one of:

* `verified`    - a record was found whose **title, authors and year** all agree with the reference
                  (and whose DOI, if both have one, is the same).
* `mismatch`    - a record was found (by DOI, or by title) but disagrees on title, authors or year.
                  This is the "real DOI, invented details" case. Reasons say which field differs.
* `not_found`   - no service returned a plausible match. **This is not proof of fabrication**: a
                  service that doesn't know a work says nothing about whether it exists, so a miss
                  at one service never becomes "hallucinated" (the ARC verifier's bug). It only
                  means "could not be confirmed", which still keeps the reference blocked.
* `unavailable` - every lookup failed, or some failed and nothing was confirmed: can't conclude, retry.
* `incomplete`  - the reference lacks a title, authors or a year, so it can't be fully checked. No
                  network call is made.

Only `verified` may ever lead to `metadata_verified` (M1.10.2). Connector failures are recorded in
`errors`, never swallowed into a verdict and never raised. A connector that can't look up by DOI
(`NotSupportedError`/`ValueError`) is skipped for that step, which is not an error. Records that
carry a `retracted` flag are reported in `flags`; being retracted doesn't make a citation fake, but
the person must see it.

Matching is deliberately strict where it affects trust: a year that differs even by one is a
mismatch (the person can still verify by hand), the first author's family name must agree, and at
least half of the reference's authors must appear in the record's list. Titles compare after the
same Unicode-safe normalisation as de-duplication, tolerating small differences (a subtitle's
punctuation, a plural) via a similarity ratio of at least `TITLE_SIMILARITY`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import Enum
from typing import Any, Iterable, Sequence

from app.connectors.base import Connector, ConnectorError, NotSupportedError, PaperRecord, SearchRequest
from app.dedupe import doi_key, first_author_key, normalise_title

TITLE_SIMILARITY = 0.92
AUTHOR_OVERLAP = 0.5
SEARCH_LIMIT = 5


class Verdict(str, Enum):
    verified = "verified"
    mismatch = "mismatch"
    not_found = "not_found"
    unavailable = "unavailable"
    incomplete = "incomplete"


@dataclass(frozen=True)
class Reference:
    title: str | None = None
    authors: tuple[str, ...] = ()
    year: int | None = None
    doi: str | None = None


@dataclass(frozen=True)
class VerificationResult:
    verdict: Verdict
    reasons: tuple[str, ...] = ()
    matched: PaperRecord | None = None  # the record that verified it, or the closest one for a mismatch
    checks: dict[str, bool] = field(default_factory=dict)  # title / authors / year / doi of `matched`
    consulted: tuple[str, ...] = ()  # connectors that answered
    errors: tuple[str, ...] = ()  # connectors that failed: "name: message"
    flags: tuple[str, ...] = ()  # e.g. ("retracted",)

    def summary(self) -> dict[str, Any]:
        """Ids and short facts only (no titles/abstracts), safe for an audit payload."""
        return {
            "verdict": self.verdict.value,
            "reasons": list(self.reasons),
            "matched": f"{self.matched.connector}:{self.matched.external_id}" if self.matched else None,
            "checks": self.checks,
            "consulted": list(self.consulted),
            "errors": [e.split(":", 1)[0] for e in self.errors],
            "flags": list(self.flags),
        }


def _same_title(a: str, b: str) -> bool:
    x, y = normalise_title(a), normalise_title(b)
    if not x or not y:
        return False
    return x == y or SequenceMatcher(None, x, y).ratio() >= TITLE_SIMILARITY


def _author_keys(authors: Iterable[str]) -> list[str]:
    return [k for k in (first_author_key([a]) for a in authors) if k]


def _compare(ref: Reference, rec: PaperRecord) -> tuple[dict[str, bool], list[str]]:
    checks: dict[str, bool] = {"title": _same_title(ref.title or "", rec.title)}
    reasons: list[str] = []
    ref_keys, rec_keys = _author_keys(ref.authors), _author_keys(rec.authors)
    if not rec_keys:
        checks["authors"] = False
        reasons.append("The record lists no authors to compare with")
    else:
        first_ok = bool(ref_keys) and ref_keys[0] == rec_keys[0]
        overlap = sum(1 for k in ref_keys if k in rec_keys) / len(ref_keys) if ref_keys else 0.0
        checks["authors"] = first_ok and overlap >= AUTHOR_OVERLAP
    checks["year"] = rec.year is not None and rec.year == ref.year
    ref_doi = doi_key(ref.doi)
    rec_doi = doi_key(rec.doi)
    checks["doi"] = not (ref_doi and rec_doi) or ref_doi == rec_doi
    wording = {
        "title": "The title differs",
        "authors": "The authors differ (first author or too few in common)",
        "year": f"The year differs (reference {ref.year}, record {rec.year})",
        "doi": "The record has a different DOI",
    }
    reasons += [wording[name] for name, ok in checks.items() if not ok and not (name == "authors" and not rec_keys)]
    return checks, reasons


def _lookups(ref: Reference, connectors: Sequence[Connector]):
    """Yield (connector_name, record_or_None, error_or_None, by_doi) for every lookup attempted."""
    doi = doi_key(ref.doi)
    if doi:
        for conn in connectors:
            try:
                yield conn.name, conn.get_by_id(doi), None, True
            except (NotSupportedError, ValueError):
                continue  # this service can't look up by DOI: not a failure
            except ConnectorError as exc:
                yield conn.name, None, str(exc), True
    for conn in connectors:
        try:
            page = conn.search(SearchRequest(query=ref.title or "", limit=SEARCH_LIMIT))
        except NotSupportedError:
            continue
        except ConnectorError as exc:
            yield conn.name, None, str(exc), False
            continue
        for record in page.records:
            yield conn.name, record, None, False
        if not page.records:
            yield conn.name, None, None, False


def verify_citation(reference: Reference, connectors: Sequence[Connector]) -> VerificationResult:
    missing = [n for n, ok in (("title", reference.title and reference.title.strip()), ("authors", _author_keys(reference.authors)),
                               ("year", reference.year)) if not ok]
    if missing:
        return VerificationResult(Verdict.incomplete, (f"The reference has no {', '.join(missing)}, so it can't be fully checked",))

    consulted: list[str] = []
    errors: list[str] = []
    best: tuple[int, PaperRecord, dict[str, bool], list[str]] | None = None
    for name, record, error, by_doi in _lookups(reference, connectors):
        if error is not None:
            errors.append(f"{name}: {error}")
            continue
        if name not in consulted:
            consulted.append(name)
        if record is None:
            continue
        checks, reasons = _compare(reference, record)
        if all(checks.values()):
            flags = tuple(f for f in record.quality_flags if f == "retracted")
            return VerificationResult(Verdict.verified, (), record, checks, tuple(consulted), tuple(dict.fromkeys(errors)), flags)
        # A record found by the reference's own DOI is evidence of a mismatch whatever its title;
        # one found by a title search only counts if its title is the reference's.
        if by_doi or checks["title"]:
            score = sum(checks.values())
            if best is None or score > best[0]:
                best = (score, record, checks, reasons)

    if best is not None:
        _, record, checks, reasons = best
        return VerificationResult(Verdict.mismatch, tuple(reasons), record, checks, tuple(consulted), tuple(dict.fromkeys(errors)))
    if errors or not consulted:
        why = "Some lookups failed and nothing could be confirmed" if errors else "No service was available to ask"
        return VerificationResult(
            Verdict.unavailable, (why + "; try again later",), consulted=tuple(consulted), errors=tuple(dict.fromkeys(errors))
        )
    return VerificationResult(
        Verdict.not_found,
        ("No service returned a matching record. This does not show the reference is fabricated, only that it could not be confirmed",),
        consulted=tuple(consulted),
    )


def verify_citations(references: Sequence[Reference], connectors: Sequence[Connector]) -> list[VerificationResult]:
    return [verify_citation(r, connectors) for r in references]
