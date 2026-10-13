"""AI quality controls for screening (M2.5, spec 5.3 "Human gate" / "Quality checks"). Pure
functions: plain data in, plain data out; no DB, no HTTP.

Two independent controls:

* **Auto-exclude** (5.3 human gate): "AI can never auto-exclude below a configurable confidence
  threshold without human sampling audit." A high-confidence AI exclude can count as the final
  decision without a person deciding that record individually, but only while the project runs an
  ongoing sample: every Nth eligible exclude (`sample_rate`) must be confirmed by a person before
  *that* exclude counts; the rest are trusted by the confidence threshold alone. An item sampled and
  overturned never counts, however high its confidence.
* **Agreement guard** (5.3 quality checks): "Track AI-human agreement; stop AI pre-screening if
  agreement drops below threshold." Reuses `app.agreement.cohen_kappa` on (AI suggestion, person's
  own eventual decision) pairs.
"""
from __future__ import annotations

from app.agreement import Agreement

# Per-decision auto-exclude status.
NOT_ELIGIBLE = "not_eligible"  # disabled, not an exclude, or below the confidence threshold
TRUSTED = "unsampled_trusted"  # eligible, not picked for the sample: counts on confidence alone
PENDING = "pending_audit"  # eligible, picked for the sample, not yet reviewed: does not count yet
CONFIRMED = "confirmed"  # eligible, sampled, a person confirmed it: counts
OVERTURNED = "overturned"  # eligible, sampled, a person overturned it: never counts

_AUDIT_STATUSES = {"pending": PENDING, "confirmed": CONFIRMED, "overturned": OVERTURNED}


def is_autoexclude_eligible(decision: str, confidence: float | None, threshold: float) -> bool:
    """Only an exclude at or above the confidence threshold is ever eligible for auto-exclude."""
    return decision == "exclude" and confidence is not None and confidence >= threshold


def sample_interval(rate: float) -> int:
    """Every Nth eligible item is sampled for audit, N = round(1/rate). `rate` is a probability in (0, 1]."""
    if not (0 < rate <= 1):
        raise ValueError("sample rate must be greater than 0 and at most 1")
    return max(1, round(1 / rate))


def due_for_sample(ordinal: int, rate: float) -> bool:
    """Whether the `ordinal`-th (1-indexed, counting this one) eligible exclude for a project is sampled."""
    if ordinal < 1:
        raise ValueError("ordinal must be >= 1")
    return ordinal % sample_interval(rate) == 0


def autoexclude_status(*, eligible: bool, audit_status: str | None) -> str:
    """The current status of one AI exclude row. `audit_status` is the DB row's status
    (`pending`/`confirmed`/`overturned`), or None if it was never sampled."""
    if not eligible:
        return NOT_ELIGIBLE
    if audit_status is None:
        return TRUSTED
    try:
        return _AUDIT_STATUSES[audit_status]
    except KeyError as exc:
        raise ValueError(f"unknown audit status: {audit_status!r}") from exc


def counts_as_excluded(status: str) -> bool:
    """Whether this status means the AI's exclude is, right now, the effective decision."""
    return status in (TRUSTED, CONFIRMED)


def should_disable_prescreen(agreement: Agreement, *, min_n: int, min_kappa: float) -> bool:
    """Whether AI pre-screen should auto-disable: enough comparable pairs exist, kappa is defined,
    and it is below the floor. An undefined kappa (too few/too uniform pairs) never triggers this —
    it is a lack of signal, not evidence of disagreement."""
    return agreement.n >= min_n and agreement.kappa is not None and agreement.kappa < min_kappa


__all__ = [
    "NOT_ELIGIBLE", "TRUSTED", "PENDING", "CONFIRMED", "OVERTURNED",
    "is_autoexclude_eligible", "sample_interval", "due_for_sample",
    "autoexclude_status", "counts_as_excluded", "should_disable_prescreen",
]
