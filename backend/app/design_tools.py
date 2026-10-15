"""Method recommender and sample-size calculators (spec 5.7 tasks 1-2, plan M4.3).

Pure functions, standard library only, no AI and no stored state: the same inputs always give the same
answer, and every number comes from the formulas below (rule 24).

Sample sizes use the usual large-sample normal approximations (with the small correction terms noted on each
function). They are close to, but not identical with, exact noncentral-t results such as G*Power's; for a
small study, confirm the final number in a dedicated tool. The response says so.
"""

from __future__ import annotations

import math
from statistics import NormalDist
from typing import Any

_NORMAL = NormalDist()

TESTS = ("two_means", "one_mean", "two_proportions", "correlation", "survey_proportion")

NOTE = (
    "Large-sample normal approximation; small studies may need a few more participants than shown. "
    "Check the final figure in a dedicated power tool before fixing the design."
)


def _z(p: float) -> float:
    return _NORMAL.inv_cdf(p)


def _check_unit(name: str, value: float) -> None:
    if not 0 < value < 1:
        raise ValueError(f"{name} must be strictly between 0 and 1")


def _alpha_z(alpha: float, sides: int) -> float:
    _check_unit("alpha", alpha)
    if sides not in (1, 2):
        raise ValueError("sides must be 1 or 2")
    return _z(1 - alpha / sides)


def _inflate(n: int, attrition: float) -> int:
    if not 0 <= attrition < 1:
        raise ValueError("attrition must be at least 0 and below 1")
    return math.ceil(n / (1 - attrition))


def two_means(d: float, alpha: float = 0.05, power: float = 0.8, sides: int = 2) -> int:
    """Participants per group to detect Cohen's d between two independent means.

    n = 2((z_a + z_b)/d)^2 + z_a^2/4 (the second term is the usual small-sample correction).
    """
    if d <= 0:
        raise ValueError("d must be greater than 0")
    _check_unit("power", power)
    za = _alpha_z(alpha, sides)
    return math.ceil(2 * ((za + _z(power)) / d) ** 2 + za**2 / 4)


def one_mean(d: float, alpha: float = 0.05, power: float = 0.8, sides: int = 2) -> int:
    """Participants for a one-sample or paired mean difference of Cohen's d: n = ((z_a + z_b)/d)^2 + z_a^2/2."""
    if d <= 0:
        raise ValueError("d must be greater than 0")
    _check_unit("power", power)
    za = _alpha_z(alpha, sides)
    return math.ceil(((za + _z(power)) / d) ** 2 + za**2 / 2)


def two_proportions(p1: float, p2: float, alpha: float = 0.05, power: float = 0.8, sides: int = 2) -> int:
    """Participants per group to tell two proportions apart (unpooled-under-H1 normal approximation)."""
    _check_unit("p1", p1)
    _check_unit("p2", p2)
    _check_unit("power", power)
    if p1 == p2:
        raise ValueError("p1 and p2 must differ")
    za = _alpha_z(alpha, sides)
    pbar = (p1 + p2) / 2
    spread = za * math.sqrt(2 * pbar * (1 - pbar)) + _z(power) * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))
    return math.ceil(spread**2 / (p1 - p2) ** 2)


def correlation(r: float, alpha: float = 0.05, power: float = 0.8, sides: int = 2) -> int:
    """Participants to detect a correlation of size |r| (Fisher z): n = ((z_a + z_b)/atanh(r))^2 + 3."""
    r = abs(r)
    if not 0 < r < 1:
        raise ValueError("r must be non-zero and between -1 and 1")
    _check_unit("power", power)
    za = _alpha_z(alpha, sides)
    return math.ceil(((za + _z(power)) / math.atanh(r)) ** 2 + 3)


def survey_proportion(
    margin: float, confidence: float = 0.95, p: float = 0.5, population: int | None = None
) -> int:
    """Respondents to estimate a proportion within +/- margin (Cochran), with finite-population correction."""
    _check_unit("margin", margin)
    _check_unit("confidence", confidence)
    _check_unit("p", p)
    z = _z(1 - (1 - confidence) / 2)
    n0 = z**2 * p * (1 - p) / margin**2
    if population is not None:
        if population < 1:
            raise ValueError("population must be at least 1")
        n0 = n0 / (1 + (n0 - 1) / population)
        return min(math.ceil(n0), population)
    return math.ceil(n0)


def sample_size(test: str, params: dict[str, Any], attrition: float = 0.0) -> dict[str, Any]:
    """Run one calculator; returns the inputs used, the analysable n and the n to recruit after attrition."""
    if test not in TESTS:
        raise ValueError(f"test must be one of: {', '.join(TESTS)}")
    required = {
        "two_means": ("d",),
        "one_mean": ("d",),
        "two_proportions": ("p1", "p2"),
        "correlation": ("r",),
        "survey_proportion": ("margin",),
    }[test]
    missing = [name for name in required if params.get(name) is None]
    if missing:
        raise ValueError(f"{test} needs: {', '.join(missing)}")
    fn = {
        "two_means": two_means,
        "one_mean": one_mean,
        "two_proportions": two_proportions,
        "correlation": correlation,
        "survey_proportion": survey_proportion,
    }[test]
    used = {k: v for k, v in params.items() if v is not None}
    n = fn(**used)
    groups = 2 if test in ("two_means", "two_proportions") else 1
    recruit = _inflate(n, attrition)
    unit = "per group" if groups == 2 else "in total"
    return {
        "test": test,
        "inputs": used,
        "attrition": attrition,
        "n_analysable": n,
        "n_to_recruit": recruit,
        "unit": unit,
        "total_to_recruit": recruit * groups,
        "note": NOTE,
    }


# ---------------------------------------------------------------- method recommender

GOALS = {
    "explore": "Understand a topic or phenomenon that is poorly understood",
    "describe": "Describe how common something is, or its characteristics, in a population",
    "relate": "Test whether and how strongly variables are associated",
    "compare_groups": "Compare outcomes between existing groups or conditions",
    "test_causal": "Test whether an intervention or factor causes an outcome",
    "understand_experience": "Understand people's experiences, meanings or processes in depth",
    "explain_with_depth": "Test a pattern and also explain why it happens",
}

_RULES: dict[str, dict[str, Any]] = {
    "explore": {
        "approach": "qualitative",
        "designs": ["Semi-structured interviews", "Focus groups", "Document or case analysis"],
        "pros": ["Surfaces constructs you did not anticipate", "Flexible as understanding grows"],
        "cons": ["Findings do not generalise statistically", "Analysis is time-intensive and interpretive"],
        "threats": ["Researcher bias", "Selective sampling"],
    },
    "describe": {
        "approach": "quantitative",
        "designs": ["Cross-sectional survey", "Secondary data analysis"],
        "pros": ["Estimates with known precision", "Cheap per respondent"],
        "cons": ["Shows association at one time, not cause", "Self-report bias"],
        "threats": ["Non-response bias", "Sampling frame coverage", "Common-method bias"],
    },
    "relate": {
        "approach": "quantitative",
        "designs": ["Cross-sectional survey with regression/SEM", "Longitudinal panel survey"],
        "pros": ["Handles many variables at once", "Panel designs give temporal order"],
        "cons": ["Cross-sectional data cannot establish causation", "Panels lose participants"],
        "threats": ["Confounding", "Common-method bias", "Attrition (panels)"],
    },
    "compare_groups": {
        "approach": "quantitative",
        "designs": ["Cross-sectional comparison", "Matched or propensity-adjusted comparison"],
        "pros": ["Uses groups that already exist", "Feasible when assignment is impossible"],
        "cons": ["Groups may differ in unmeasured ways"],
        "threats": ["Selection bias", "Confounding"],
    },
    "test_causal": {
        "approach": "quantitative",
        "designs": ["Randomised controlled experiment", "Quasi-experiment (difference-in-differences, regression discontinuity)"],
        "pros": ["Strongest basis for causal claims when randomised"],
        "cons": ["Costly and sometimes unethical or impractical", "Artificial settings limit generalisation"],
        "threats": ["Attrition", "Contamination between conditions", "Demand effects"],
    },
    "understand_experience": {
        "approach": "qualitative",
        "designs": ["Phenomenological interviews", "Ethnography", "Case study"],
        "pros": ["Rich detail on meaning and context", "Participants' own framing"],
        "cons": ["Small, non-random samples", "Hard to replicate exactly"],
        "threats": ["Researcher reflexivity", "Social-desirability in interviews"],
    },
    "explain_with_depth": {
        "approach": "mixed",
        "designs": ["Explanatory sequential (survey, then interviews)", "Convergent parallel"],
        "pros": ["Numbers show the pattern, interviews explain it", "Cross-checks each method"],
        "cons": ["Roughly double the work", "Needs skills in both traditions", "Integration is hard to do well"],
        "threats": ["Weak integration of strands", "Mismatched samples between strands"],
    },
}


def recommend_method(
    goal: str, *, can_randomize: bool | None = None, accessible_population: bool | None = None
) -> dict[str, Any]:
    """A rule-based first suggestion, with trade-offs. The scholar decides; nothing here is a verdict."""
    if goal not in _RULES:
        raise ValueError(f"goal must be one of: {', '.join(GOALS)}")
    rule = _RULES[goal]
    designs = list(rule["designs"])
    cautions: list[str] = []
    approach = rule["approach"]
    if goal == "test_causal":
        if can_randomize is False:
            designs = [d for d in designs if d.startswith("Quasi")]
            cautions.append("Random assignment is not possible, so claims must be framed as causal-leaning, not proven.")
        elif can_randomize is None:
            cautions.append("State whether you can randomise: it decides between an experiment and a quasi-experiment.")
    if accessible_population is False and approach in ("quantitative", "mixed"):
        cautions.append("Without access to a sampling frame, probability sampling is not possible; say so in the limitations.")
    return {
        "goal": goal,
        "goal_description": GOALS[goal],
        "approach": approach,
        "candidate_designs": designs,
        "advantages": rule["pros"],
        "disadvantages": rule["cons"],
        "threats_to_validity": rule["threats"],
        "cautions": cautions,
        "note": "Rule-based suggestion from the research goal only. The scholar chooses the design (gate G7 still applies).",
    }
