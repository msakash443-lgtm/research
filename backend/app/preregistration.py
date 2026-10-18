"""Pre-registration draft (M4.6): AsPredicted-style answers and OSF-style sections built from stored RQs and hypotheses.

Pure and stateless: nothing is stored, submitted or sent anywhere, and no model is called. Text the researcher
already wrote (questions, hypothesis statements) is copied as it is. Everything the app cannot know (sample size,
exclusions, analysis plan, whether data already exist) is bracketed for the researcher to complete, never guessed.
Filing the pre-registration is the researcher's act; approving the hypotheses is gate G6, which this does not touch.
"""

from __future__ import annotations

from dataclasses import dataclass, field

FILL = "[{}: to complete]"
NOTE = (
    "Draft only: not submitted to AsPredicted or OSF. Complete the bracketed fields, check the current form "
    "wording on aspredicted.org and osf.io, then copy the answers in and file it yourself."
)


@dataclass(frozen=True)
class RQ:
    text: str
    type: str


@dataclass(frozen=True)
class HypothesisItem:
    statement: str
    direction: str
    rq_text: str  # the question it belongs to, matched by text


@dataclass(frozen=True)
class Draft:
    title: str
    main_hypothesis: str
    data_collected: str
    sample_size: str
    exclusions: str
    unhypothesised_questions: list[str] = field(default_factory=list)
    hypothesis_count: int = 0
    aspredicted: dict[str, str] = field(default_factory=dict)
    osf_sections: list[dict[str, str]] = field(default_factory=list)
    note: str = NOTE


def _clean(value: str) -> str:
    return " ".join(value.split())


def build_preregistration(title: str, research_questions: list[RQ], hypotheses: list[HypothesisItem]) -> Draft:
    study = _clean(title) or FILL.format("study title")
    hypothesised = {_clean(h.rq_text) for h in hypotheses}
    if hypotheses:
        main = "\n".join(f"H{i}: {_clean(h.statement)} (expected direction: {_clean(h.direction)})" for i, h in enumerate(hypotheses, 1))
    else:
        main = FILL.format("main hypothesis")
    unhypothesised = [_clean(q.text) for q in research_questions if _clean(q.text) not in hypothesised]
    data_collected = FILL.format("data already collected")
    sample_size = FILL.format("sample size")
    exclusions = FILL.format("exclusion criteria")
    dependent = FILL.format("dependent variables and how they are measured")
    conditions = FILL.format("conditions or groups, and how participants are assigned")
    analyses = FILL.format("exact analyses for each hypothesis")
    secondary = (
        "; ".join(unhypothesised) + " (exploratory; not pre-registered as hypotheses)"
        if unhypothesised else FILL.format("secondary or exploratory analyses")
    )
    aspredicted = {
        "data_collected": data_collected,
        "data_looked_at": FILL.format("whether any data have been inspected"),
        "main_hypothesis": main,
        "dependent_variables": dependent,
        "conditions": conditions,
        "analyses": analyses,
        "secondary_analyses": secondary,
        "sample_size": sample_size,
        "other": FILL.format("anything else"),
    }
    osf_sections = [
        {"heading": "Study information", "body": f"Title: {study}"},
        {"heading": "Hypotheses", "body": main},
        {"heading": "Design plan", "body": f"{conditions}. Dependent variables: {dependent}."},
        {"heading": "Sampling plan", "body": f"Sample size: {sample_size}. Exclusions: {exclusions}."},
        {"heading": "Analysis plan", "body": analyses},
        {"heading": "Other", "body": f"Data already collected: {data_collected}. Exploratory questions: {secondary}."},
    ]
    return Draft(
        title=study,
        main_hypothesis=main,
        data_collected=data_collected,
        sample_size=sample_size,
        exclusions=exclusions,
        unhypothesised_questions=unhypothesised,
        hypothesis_count=len(hypotheses),
        aspredicted=aspredicted,
        osf_sections=osf_sections,
    )
