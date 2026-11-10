"""Substantive research-question critique (X.191): checks that actually read the question text.

Pure: no database, no HTTP, no model call. `run` takes plain data (the question text plus facts the
caller already looked up) and returns a list of `{check, status, reason}` dicts, `status` one of
`pass`, `warn`, `human_review`. The caller (`routers/research_questions.py`) merges this list into the
advisory FINER response; it never replaces human judgment.
"""

from __future__ import annotations

import re

from app.construct_consistency import find_spelling_variants

_QUESTION_WORDS = ("what", "how", "why", "when", "where", "who", "which", "whether", "does", "do", "is", "are", "can", "will", "should")
_VAGUE_WORDS = ("impact", "role", "factors", "factor", "effects", "effect")

# Research type names (app/research_types/*.toml) classified as quantitative or qualitative for the
# framework check. Types that mix both (mixed_methods) or name no particular design (full_journey,
# literature_review) are left unclassified: the framework check becomes human_review for them.
QUANTITATIVE_TYPES = frozenset({"quantitative_survey", "experimental"})
QUALITATIVE_TYPES = frozenset({"qualitative"})

# Framework element groups, keyed by the element name screening criteria already use (app/criteria.py's
# `FRAMEWORKS`), each with the PECO/PEO synonym the same element also accepts.
_QUANT_FRAMEWORK: dict[str, tuple[str, ...]] = {
    "population": (),
    "intervention": ("exposure",),
    "comparison": ("comparator",),
    "outcome": (),
}
_QUAL_FRAMEWORK: dict[str, tuple[str, ...]] = {
    "sample": ("population",),
    "phenomenon_of_interest": ("exposure", "phenomenon"),
    "design": (),
    "evaluation": ("outcome",),
}


def _term_pattern(term: str) -> re.Pattern[str]:
    return re.compile(r"\b" + re.escape(term.strip()) + r"\b", re.IGNORECASE)


def _contains_any(text: str, terms: list[str]) -> bool:
    return any(_term_pattern(term).search(text) for term in terms if term.strip())


def _check_question_form(question_text: str) -> dict[str, str]:
    count = question_text.count("?")
    if count == 0:
        return {"check": "question_form", "status": "warn", "reason": "the question text has no '?'"}
    if count > 1:
        return {"check": "question_form", "status": "warn", "reason": "the question text has more than one '?'"}
    return {"check": "question_form", "status": "pass", "reason": "the question text is a single interrogative sentence"}


_JOINED_AFTER_MARK = re.compile(r"\?\s*(and|or)\b", re.IGNORECASE)
_TWO_CLAUSES = re.compile(
    r"\b(" + "|".join(_QUESTION_WORDS) + r")\b.{3,}?\b(and|or)\b\s*(" + "|".join(_QUESTION_WORDS) + r")\b",
    re.IGNORECASE,
)


def _check_double_barrelled(question_text: str) -> dict[str, str]:
    if _JOINED_AFTER_MARK.search(question_text) or _TWO_CLAUSES.search(question_text):
        return {"check": "double_barrelled", "status": "warn",
                "reason": "the question text joins two separate questions with 'and'/'or'"}
    return {"check": "double_barrelled", "status": "pass", "reason": "the question text asks a single thing"}


def _framework_elements(research_type: str | None) -> dict[str, tuple[str, ...]] | None:
    if research_type in QUANTITATIVE_TYPES:
        return _QUANT_FRAMEWORK
    if research_type in QUALITATIVE_TYPES:
        return _QUAL_FRAMEWORK
    return None


def _check_framework_elements(question_text: str, research_type: str | None, criteria_terms: dict[str, list[str]]) -> dict[str, str]:
    elements = _framework_elements(research_type)
    if elements is None:
        return {"check": "framework_elements", "status": "human_review",
                "reason": "the project has no quantitative or qualitative research type set, so PICO/PECO or SPIDER/PEO elements cannot be checked"}
    if not criteria_terms:
        return {"check": "framework_elements", "status": "human_review",
                "reason": "the project has no saved screening criteria terms to check the framework elements against"}
    missing = []
    for element, synonyms in elements.items():
        terms = list(criteria_terms.get(element, []))
        for synonym in synonyms:
            terms += criteria_terms.get(synonym, [])
        if not terms or not _contains_any(question_text, terms):
            missing.append(element)
    if missing:
        return {"check": "framework_elements", "status": "warn",
                "reason": "the question text does not mention the saved criteria terms for: " + ", ".join(missing)}
    return {"check": "framework_elements", "status": "pass",
            "reason": "the question text mentions the saved criteria terms for every framework element"}


def _check_vague_scope(question_text: str, criteria_terms: dict[str, list[str]]) -> dict[str, str]:
    found_vague = [word for word in _VAGUE_WORDS if _term_pattern(word).search(question_text)]
    if not found_vague:
        return {"check": "vague_scope", "status": "pass", "reason": "the question text does not use vague scope language"}
    outcome_terms = list(criteria_terms.get("outcome", [])) + list(criteria_terms.get("evaluation", []))
    if outcome_terms and _contains_any(question_text, outcome_terms):
        return {"check": "vague_scope", "status": "pass",
                "reason": "the question text names a specific outcome alongside its scope language"}
    return {"check": "vague_scope", "status": "warn",
            "reason": "the question text uses vague scope language (" + ", ".join(found_vague) + ") without a named outcome"}


def _check_constructs(question_text: str, construct_names: list[str] | None) -> dict[str, str]:
    if construct_names is None:
        return {"check": "constructs", "status": "human_review", "reason": "no construct registry is loaded for this project"}
    if not construct_names:
        return {"check": "constructs", "status": "pass", "reason": "the project's construct registry is empty"}
    variants = find_spelling_variants({"registry": "; ".join(construct_names), "question": question_text})
    if variants:
        mismatched = ", ".join(variant["construct"] for variant in variants)
        return {"check": "constructs", "status": "warn",
                "reason": "the question text spells a registered construct differently than the registry: " + mismatched}
    return {"check": "constructs", "status": "pass", "reason": "constructs used in the question text match the registry's spelling"}


def _check_answerability(has_linked_selected_gap_or_verified_source: bool) -> dict[str, str]:
    if has_linked_selected_gap_or_verified_source:
        return {"check": "answerability", "status": "pass",
                "reason": "the question is linked to a selected gap or a verified source"}
    return {"check": "answerability", "status": "warn",
            "reason": "the question has no linked selected gap and no verified source"}


def run(
    question_text: str,
    *,
    research_type: str | None,
    criteria_terms: dict[str, list[str]],
    construct_names: list[str] | None,
    has_linked_selected_gap_or_verified_source: bool,
) -> list[dict[str, str]]:
    """The substantive checks for one research question's text (status `pass|warn|human_review`, with a reason)."""
    return [
        _check_question_form(question_text),
        _check_double_barrelled(question_text),
        _check_framework_elements(question_text, research_type, criteria_terms),
        _check_vague_scope(question_text, criteria_terms),
        _check_constructs(question_text, construct_names),
        _check_answerability(has_linked_selected_gap_or_verified_source),
    ]
