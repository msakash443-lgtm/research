"""Evaluation harness metrics (plan X.5; spec 7.7 and 11). Pure functions: plain data in, plain data out.

The gold data is the scholar's own labelling (`tests/gold`, X.15). The runner (`backend/eval/run_eval.py`)
calls the real AI step on every gold item and hands the answers to these functions; nothing here calls a
model, and nothing is ever filled in for an item the AI failed on.

Screening (spec 11: AI-human agreement kappa >= 0.8, false-exclusion rate < 2%):
* kappa is computed on the items the AI decided (include/exclude). A "maybe" goes to a person, so it is
  not a disagreement, but it is not agreement either: it is left out of kappa and reported as
  `maybe_rate` next to it, so a model can't look good by answering "maybe" to every hard item unseen.
* a false exclusion is a gold "include" the AI suggested excluding, over all gold includes. A "maybe"
  on a gold include is not a false exclusion (a person still sees the paper).

Extraction (spec 11: field-level precision/recall >= 90% on critical fields): per field, an AI value equal
to the gold value is a true positive; an AI value that is wrong or not in the gold is a false positive;
a gold value the AI missed or got wrong is a false negative. Values are compared after light
normalisation (case, whitespace, numeric type), never by similarity.

Any AI failure, or a metric that can't be computed (no gold includes, no gold values for a critical
field), makes the result `incomplete`, never `passed`.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from app.agreement import cohen_kappa, meets_threshold

KAPPA_TARGET = 0.8
FALSE_EXCLUSION_LIMIT = 0.02  # strictly below (spec: "< 2%")
EXTRACTION_TARGET = 0.9

PASSED, FAILED, INCOMPLETE = "passed", "failed", "incomplete"


def dataset_hash(*parts: Any) -> str:
    """A stable hash of the evaluation inputs (gold set, criteria, ...): a report is only valid for these."""
    canonical = json.dumps(parts, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---- screening ----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class AiScreen:
    """What the AI step returned for one gold paper: a decision, or the error that stopped it."""

    decision: str | None = None  # include | exclude | maybe
    error: str | None = None


@dataclass
class ScreeningEval:
    n: int
    decided: int
    maybe: int
    failed: int
    kappa: float | None
    percent_agreement: float | None
    maybe_rate: float | None
    false_exclusions: list[str]
    false_exclusion_rate: float | None
    status: str
    problems: list[str] = field(default_factory=list)
    kappa_note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "n": self.n, "decided": self.decided, "maybe": self.maybe, "failed": self.failed,
            "kappa": self.kappa, "percent_agreement": self.percent_agreement, "kappa_note": self.kappa_note,
            "maybe_rate": self.maybe_rate, "false_exclusions": self.false_exclusions,
            "false_exclusion_rate": self.false_exclusion_rate,
            "targets": {"kappa_min": KAPPA_TARGET, "false_exclusion_rate_below": FALSE_EXCLUSION_LIMIT},
            "status": self.status, "problems": self.problems,
        }


def screening_eval(gold: Mapping[str, str], ai: Mapping[str, AiScreen]) -> ScreeningEval:
    """Compare the AI's suggestions with the gold labels (`paper id -> include|exclude`)."""
    problems: list[str] = []
    unknown = sorted(set(ai) - set(gold))
    if unknown:
        raise ValueError(f"AI answers for papers that are not in the gold set: {unknown[:5]}")
    for paper_id, label in gold.items():
        if label not in ("include", "exclude"):
            raise ValueError(f"gold label for {paper_id!r} must be include or exclude, not {label!r}")

    pairs, maybe, failed, false_exclusions = [], 0, 0, []
    for paper_id, label in gold.items():
        answer = ai.get(paper_id)
        if answer is None or answer.error is not None or answer.decision is None:
            failed += 1
            continue
        if answer.decision not in ("include", "exclude", "maybe"):
            raise ValueError(f"AI decision for {paper_id!r} must be include, exclude or maybe, not {answer.decision!r}")
        if answer.decision == "maybe":
            maybe += 1
        else:
            pairs.append((label, answer.decision))
        if label == "include" and answer.decision == "exclude":
            false_exclusions.append(paper_id)

    n = len(gold)
    agreement = cohen_kappa(pairs)
    includes = sum(1 for label in gold.values() if label == "include")
    answered = n - failed
    fer = len(false_exclusions) / includes if includes else None

    if n == 0:
        problems.append("the gold set has no papers")
    if failed:
        problems.append(f"the AI step failed on {failed} of {n} papers")
    if fer is None and n:
        problems.append("no gold 'include' papers, so the false-exclusion rate is undefined")
    if agreement.kappa is None and n:
        problems.append(f"kappa is undefined: {agreement.note}")

    if problems:
        status = INCOMPLETE
    elif meets_threshold(agreement.kappa, KAPPA_TARGET) and fer < FALSE_EXCLUSION_LIMIT:
        status = PASSED
    else:
        status = FAILED
    return ScreeningEval(
        n=n, decided=len(pairs), maybe=maybe, failed=failed,
        kappa=agreement.kappa, percent_agreement=agreement.percent_agreement,
        maybe_rate=(maybe / answered) if answered else None,
        false_exclusions=false_exclusions, false_exclusion_rate=fer,
        status=status, problems=problems, kappa_note=agreement.note,
    )


# ---- extraction ---------------------------------------------------------------------------------------

def _norm(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = " ".join(value.split()).casefold()
        try:
            number = float(text)
        except ValueError:
            return text
        return number if math.isfinite(number) else text
    if isinstance(value, (list, tuple)):
        return tuple(_norm(v) for v in value)
    if isinstance(value, dict):
        return tuple(sorted((str(k), _norm(v)) for k, v in value.items()))
    return value


def same_value(a: Any, b: Any) -> bool:
    """Gold and AI values match after normalising case, whitespace and numeric type."""
    na, nb = _norm(a), _norm(b)
    if isinstance(na, bool) or isinstance(nb, bool):
        return type(na) is type(nb) and na == nb  # True must not equal 1.0
    if isinstance(na, float) and isinstance(nb, float):
        return math.isclose(na, nb, rel_tol=1e-9, abs_tol=1e-12)
    return na == nb


@dataclass
class FieldScore:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float | None:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else None

    @property
    def recall(self) -> float | None:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else None


@dataclass
class ExtractionEval:
    fields: dict[str, FieldScore]
    critical: tuple[str, ...]
    failed: list[str]
    status: str
    problems: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "fields": {
                name: {"tp": s.tp, "fp": s.fp, "fn": s.fn, "precision": s.precision, "recall": s.recall,
                       "critical": name in self.critical}
                for name, s in sorted(self.fields.items())
            },
            "failed": self.failed,
            "targets": {"critical_precision_min": EXTRACTION_TARGET, "critical_recall_min": EXTRACTION_TARGET},
            "status": self.status, "problems": self.problems,
        }


def extraction_eval(
    gold: Mapping[str, Mapping[str, Any]],
    ai: Mapping[str, Mapping[str, Any] | None],
    critical: Iterable[str],
) -> ExtractionEval:
    """Score AI extractions against gold ones (`paper id -> field -> value`; a gold `None` = not in the paper).

    Only fields the gold lists for a paper are scored for it. `ai[paper] is None` (or missing) means
    the AI step failed for that paper.
    """
    critical = tuple(sorted(set(critical)))
    scores: dict[str, FieldScore] = {}
    failed: list[str] = []
    unknown = sorted(set(ai) - set(gold))
    if unknown:
        raise ValueError(f"AI extractions for papers that are not in the gold set: {unknown[:5]}")
    for paper_id, gold_fields in gold.items():
        answer = ai.get(paper_id)
        if answer is None:
            failed.append(paper_id)
            continue
        for name, gold_value in gold_fields.items():
            score = scores.setdefault(name, FieldScore())
            ai_value = answer.get(name)
            if gold_value is None and ai_value is None:
                continue  # correctly abstained; nothing to count
            if gold_value is not None and ai_value is not None and same_value(gold_value, ai_value):
                score.tp += 1
                continue
            if ai_value is not None:
                score.fp += 1
            if gold_value is not None:
                score.fn += 1

    problems: list[str] = []
    if not gold:
        problems.append("the gold set has no verified extractions")
    if failed:
        problems.append(f"the AI step failed on {len(failed)} of {len(gold)} papers")
    if not critical:
        problems.append("no critical fields were named, so there is nothing to pass or fail")
    for name in critical:
        s = scores.get(name)
        if s is None or s.precision is None or s.recall is None:
            problems.append(f"critical field {name!r} can't be scored (no gold or AI values)")

    if problems:
        status = INCOMPLETE
    elif all(scores[n].precision >= EXTRACTION_TARGET and scores[n].recall >= EXTRACTION_TARGET for n in critical):
        status = PASSED
    else:
        status = FAILED
    return ExtractionEval(scores, critical, failed, status, problems)
