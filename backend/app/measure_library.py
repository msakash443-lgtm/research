"""Validated-measure library for the instrument builder (M4.4.2).

Each entry is citation metadata only, approved by the owner (2026-10-17) from a shortlist. Item wording is NOT stored:
it is copyrighted or unverified here, so the scholar copies it from the source after checking its licence. Every
licence is `to check` until the owner confirms it. Nothing in this module chooses an instrument for a project.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Measure:
    key: str
    name: str
    construct: str
    authors: str
    year: int
    citation: str
    doi: str | None = None
    pmid: str | None = None
    licence: str = "to check"


MEASURES: tuple[Measure, ...] = (
    Measure(
        key="bfi",
        name="Big Five Inventory",
        construct="Big Five personality traits",
        authors="John & Srivastava",
        year=1999,
        citation=(
            "John, O. P., & Srivastava, S. (1999). The Big-Five trait taxonomy: History, measurement, and theoretical "
            "perspectives. In L. A. Pervin & O. P. John (Eds.), Handbook of personality: Theory and research (2nd ed., pp. 102–138). "
            "Guilford Press."
        ),
    ),
    Measure(
        key="mbi",
        name="Maslach Burnout Inventory",
        construct="Burnout: exhaustion, cynicism, professional efficacy",
        authors="Maslach & Jackson",
        year=1981,
        citation=(
            "Maslach, C., & Jackson, S. E. (1981). The measurement of experienced burnout. "
            "Journal of Occupational Behaviour, 2, 99–113."
        ),
    ),
    Measure(
        key="phq9",
        name="Patient Health Questionnaire-9 (PHQ-9)",
        construct="Depression severity",
        authors="Kroenke, Spitzer & Williams",
        year=2001,
        citation=(
            "Kroenke, K., Spitzer, R. L., & Williams, J. B. W. (2001). The PHQ-9: Validity of a brief depression severity "
            "measure. Journal of General Internal Medicine, 16(9), 606–613."
        ),
        doi="10.1046/j.1525-1497.2001.016009606.x",
        pmid="11556941",
    ),
    Measure(
        key="pss",
        name="Perceived Stress Scale",
        construct="Perceived stress",
        authors="Cohen, Kamarck & Mermelstein",
        year=1983,
        citation=(
            "Cohen, S., Kamarck, T., & Mermelstein, R. (1983). A global measure of perceived stress. "
            "Journal of Health and Social Behavior, 24(4), 385–396."
        ),
        pmid="6668417",
    ),
    Measure(
        key="ucla_loneliness",
        name="UCLA Loneliness Scale (Version 3)",
        construct="Loneliness",
        authors="Russell",
        year=1996,
        citation=(
            "Russell, D. W. (1996). UCLA Loneliness Scale (Version 3): Reliability, validity, and factor structure. "
            "Journal of Personality Assessment, 66(1), 20–40."
        ),
        doi="10.1207/s15327752jpa6601_2",
    ),
    Measure(
        key="rses",
        name="Rosenberg Self-Esteem Scale",
        construct="Global self-esteem",
        authors="Rosenberg",
        year=1965,
        citation="Rosenberg, M. (1965). Society and the adolescent self-image. Princeton University Press.",
    ),
)

BY_KEY = {m.key: m for m in MEASURES}


def measure(key: str) -> Measure | None:
    return BY_KEY.get(key)


def build_draft(keys: list[str], own_items: list[str]) -> dict:
    """A draft instrument: the chosen measures' references and the scholar's own items, with no item wording copied in.

    Raises KeyError for a key that is not in the library, so the route can refuse it rather than guess.
    """
    chosen: list[Measure] = []
    for key in keys:
        if key not in BY_KEY:
            raise KeyError(key)
        if BY_KEY[key] not in chosen:
            chosen.append(BY_KEY[key])
    return {
        "measures": [
            {
                "key": m.key,
                "name": m.name,
                "construct": m.construct,
                "citation": m.citation,
                "doi": m.doi,
                "pmid": m.pmid,
                "licence": m.licence,
            }
            for m in chosen
        ],
        "own_items": list(own_items),
        "reference_list": sorted(m.citation for m in chosen),
        "note": (
            "Item wording from a published measure is not included. Copy it from the source only after checking the "
            "licence (every licence is 'to check'). Nothing here approves an instrument or a gate."
        ),
    }
