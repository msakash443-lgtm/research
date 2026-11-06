"""Item-quality checks for survey and interview items (M4.4.1, spec 5.7 item quality).

Pure and stateless: regular expressions only, no model, nothing stored. They flag wording that often biases
answers (leading items) and items that may ask two things at once (double-barrelled items). A flag is a prompt to
read the item again, not a verdict: the researcher decides whether to rewrite it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MAX_ITEM_CHARS = 1000

_LEADING = [
    (
        re.compile(r"\b(don'?t|do not|isn'?t|is not|aren'?t|wouldn'?t|would not|doesn'?t|didn'?t)\s+(you|it|they|we|one)\b", re.I),
        "A negative tag or question pushes the respondent towards agreeing.",
    ),
    (
        re.compile(r"\b(most|many|everyone|everybody|all)\s+(people|of us|users|experts|agree|think|believe)\b", re.I),
        "An appeal to what others think pressures the respondent.",
    ),
    (
        re.compile(r"\bas we all know\b|\beveryone knows\b|\bit is (obvious|clear) that\b", re.I),
        "The wording presents the claim as settled, so disagreeing seems unreasonable.",
    ),
    (
        re.compile(
            r"\bhow (much|great|bad) (do|did) you (love|hate|like|enjoy|dislike|despise|support|oppose|value|resent|fear|trust)\b",
            re.I,
        ),
        "The question assumes an answer (that the respondent has a strong view).",
    ),
]

_DOUBLE = re.compile(r"\b(and|or)\b", re.I)
# Pasted items often carry curly apostrophes; the patterns above match only the straight one (review defect, M4.4.1).
_APOSTROPHES = str.maketrans({"‘": "'", "’": "'", "ʼ": "'"})


@dataclass(frozen=True)
class Flag:
    kind: str  # "leading" or "double_barrelled"
    matched: str
    reason: str


@dataclass(frozen=True)
class ItemResult:
    text: str
    flags: list[Flag] = field(default_factory=list)


def check_item(text: str) -> ItemResult:
    cleaned = " ".join((text or "").split())
    if not cleaned:
        raise ValueError("An item must not be blank.")
    if len(cleaned) > MAX_ITEM_CHARS:
        raise ValueError(f"An item must be at most {MAX_ITEM_CHARS} characters.")
    flags: list[Flag] = []
    matchable = cleaned.translate(_APOSTROPHES)
    for pattern, why in _LEADING:
        for match in pattern.finditer(matchable):
            flags.append(Flag("leading", match.group(0), f"{why} The researcher decides whether to reword it."))
    for match in _DOUBLE.finditer(cleaned):
        flags.append(
            Flag(
                "double_barrelled",
                match.group(0),
                f"Joins two ideas with '{match.group(0)}'. If they could be answered differently, split the item. "
                "The researcher decides whether it is really one construct.",
            )
        )
    return ItemResult(cleaned, flags)
