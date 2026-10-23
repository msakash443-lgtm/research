"""Overclaiming check for one manuscript section (M6.7, spec §5.10). Pure: text in, findings out, no database or network.

Two kinds of finding, each tied to the sentence that triggered it:

- `causal_language`: wording that asserts a cause ("causes", "leads to", "results in", "the effect of") in a study whose
  design cannot show causation. Only raised when the design is not `experimental`.
- `overgeneralization`: wording that reaches past any sample ("all", "always", "never", "everyone", "universally",
  "proves"), with the "all/every" family counted only in front of a noun-like word so "all of the" is not flagged twice.

Findings are advisory. The checker never blocks an export (that is M6.4), never edits the text, and a sentence that
hedges ("may", "might", "suggests", "is associated with") is not flagged for causal language. It is a wording scan, not
an understanding of the study: a flag means "look at this sentence", not "this sentence is wrong".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

DESIGNS = ("observational", "experimental", "unspecified")

_CAUSAL = re.compile(
    r"\b(causes?|caused|causing|leads?\s+to|led\s+to|leading\s+to|results?\s+in|resulted\s+in|"
    r"(?:the\s+)?(?:effect|impact|influence)\s+of|due\s+to|because\s+of|drives?|driven\s+by|"
    r"(?:increase[sd]?|decrease[sd]?|reduce[sd]?|improve[sd]?|raise[sd]?|lowered|boost(?:s|ed)?)\s+(?!with\b))",
    re.IGNORECASE,
)
_GENERAL = re.compile(
    r"\b(always|never|everyone|everybody|nobody|universally|in\s+all\s+cases|without\s+exception|"
    r"prov(?:es?|ed|ing)|(?:all|every)\s+(?:people|adults|women|men|children|students|workers|firms|countries|"
    r"populations?|contexts?|settings?|cultures?|participants|patients|individuals|humans))\b",
    re.IGNORECASE,
)
_HEDGE = re.compile(
    r"\b(may|might|could|can\s+be|appears?\s+to|seems?\s+to|suggests?|suggested|consistent\s+with|associated\s+with|"
    r"linked\s+to|related\s+to|correlat\w+|tend(?:s|ed)?\s+to|possibly|potentially|likely)\b",
    re.IGNORECASE,
)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")


@dataclass(frozen=True)
class Finding:
    kind: str
    subject: str
    message: str


@dataclass
class OverclaimReport:
    design: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings


def sentences(text: str) -> list[str]:
    """Sentences of a text, split at ./!/? followed by a capital, digit, quote or bracket; blank lines are dropped."""
    out: list[str] = []
    for line in text.splitlines():
        for part in _SENTENCE_END.split(line.strip()):
            part = part.strip()
            if part:
                out.append(part)
    return out


def _excerpt(sentence: str, limit: int = 160) -> str:
    return sentence if len(sentence) <= limit else sentence[: limit - 1].rstrip() + "…"


def check_text(text: str, design: str = "observational") -> OverclaimReport:
    """Scan `text` for overclaiming. `design` is one of DESIGNS; unknown values raise ValueError so a typo cannot
    silently switch the causal check off."""
    if design not in DESIGNS:
        raise ValueError(f"design must be one of {', '.join(DESIGNS)}")
    report = OverclaimReport(design=design)
    for sentence in sentences(text):
        subject = _excerpt(sentence)
        if design != "experimental" and not _HEDGE.search(sentence):
            match = _CAUSAL.search(sentence)
            if match:
                wording = match.group(0).strip()
                if design == "unspecified":
                    message = (f"'{wording}' states a cause, but the study design is not stated; name the design, "
                               "and say 'associated with' unless the design can show causation.")
                else:
                    message = (f"'{wording}' states a cause, but an {design} design cannot show causation; "
                               "say 'associated with' or name the design's limit.")
                report.findings.append(Finding(kind="causal_language", subject=subject, message=message))
        general = _GENERAL.search(sentence)
        if general:
            report.findings.append(Finding(
                kind="overgeneralization", subject=subject,
                message=f"'{general.group(0).strip()}' reaches beyond any sample; state who the finding covers.",
            ))
    return report
