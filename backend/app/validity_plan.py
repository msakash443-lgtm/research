"""Validity and reliability plan template (M4.4.3). Pure: plain data in, a plan skeleton out.

The plan names the kinds of evidence a scholar normally reports and leaves every decision (thresholds, sample, method
choice) as a bracketed field. It never states a published reliability or validity figure, never states that an
instrument is valid for the project, and approves nothing.
"""

from __future__ import annotations

from app.measure_library import BY_KEY

TO_DECIDE = "[{}: to decide and justify]"

RELIABILITY_CHECKS = (
    ("internal_consistency", "Internal consistency in your own sample (for example Cronbach's alpha or McDonald's omega, per scale or subscale)."),
    ("item_analysis", "Item analysis: item-total correlations and whether dropping any item changes the estimate."),
    ("stability", "Stability over time (test-retest) if the construct is meant to be stable and a second wave is feasible."),
)

VALIDITY_CHECKS = (
    ("content", "Content validity: expert or target-population review that the items cover the construct."),
    ("structure", "Structural validity: whether the factor structure you analyse matches the one the source reports."),
    ("convergent_discriminant", "Convergent and discriminant evidence against measures you name in advance."),
    ("criterion_or_known_groups", "Criterion or known-groups evidence where a suitable criterion or contrast exists."),
)

OWN_ITEM_STEPS = (
    "Expert review of the draft items for coverage and wording.",
    "Cognitive interviews or a small pilot with people like the target respondents.",
    "Run the item-quality checks (leading and double-barrelled wording) and revise.",
    "Examine the structure and reliability in the pilot before the main data collection.",
)


def build_plan(measure_keys: list[str], own_item_count: int) -> dict:
    """Raises KeyError for an unknown measure key and ValueError when there is nothing to plan for."""
    if own_item_count < 0:
        raise ValueError("own_item_count cannot be negative")
    chosen = []
    for key in measure_keys:
        if key not in BY_KEY:
            raise KeyError(key)
        if BY_KEY[key] not in chosen:
            chosen.append(BY_KEY[key])
    if not chosen and own_item_count == 0:
        raise ValueError("Name at least one measure or give a number of own items to plan for")

    instruments = [
        {
            "key": m.key,
            "name": m.name,
            "construct": m.construct,
            "citation": m.citation,
            "source_evidence": (
                "Evidence reported in the source above applies to the samples and language it studied. "
                "Re-check it for your population; do not present it as evidence from your own data."
            ),
            "language_or_adaptation": "[language/version used and any translation or adaptation: to complete]",
        }
        for m in chosen
    ]
    return {
        "instruments": instruments,
        "own_items": {
            "count": own_item_count,
            "steps": list(OWN_ITEM_STEPS) if own_item_count else [],
        },
        "reliability": [{"id": i, "check": text, "planned": TO_DECIDE.format("method and statistic")} for i, text in RELIABILITY_CHECKS],
        "validity": [{"id": i, "check": text, "planned": TO_DECIDE.format("evidence and comparison measures")} for i, text in VALIDITY_CHECKS],
        "decisions": [
            TO_DECIDE.format("acceptable-reliability threshold"),
            TO_DECIDE.format("sample size for the psychometric analysis"),
            TO_DECIDE.format("what you will do if a scale performs poorly in your sample"),
        ],
        "note": (
            "A template of evidence to plan for. No threshold, sample size or published figure is supplied, and nothing "
            "here says an instrument is valid for your study or approves a gate."
        ),
    }
