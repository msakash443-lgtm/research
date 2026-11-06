"""Method recommender and sample-size calculators (spec 5.7 tasks 1-2, plan M4.3).

Pure functions, standard library only, no AI and no stored state: the same inputs always give the same
answer, and every number comes from the formulas below (rule 24).

Sample sizes use the usual large-sample normal approximations (with the small correction terms noted on each
function). They are close to, but not identical with, exact noncentral-t results such as G*Power's; for a
small study, confirm the final number in a dedicated tool. The response says so.

The ANOVA and regression calculators (M4.3.1) are exact instead: they search for the smallest N whose
noncentral-F power reaches the target, as G*Power does.
"""

from __future__ import annotations

import math
from statistics import NormalDist
from typing import Any

_NORMAL = NormalDist()

TESTS = ("two_means", "one_mean", "two_proportions", "correlation", "survey_proportion", "anova_oneway", "regression")

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


# ---------------------------------------------------------------- exact noncentral F (M4.3.1)

EXACT_NOTE = "Exact noncentral-F power (fixed-effects, equal group sizes); should match G*Power to the participant."


def _beta_cf(a: float, b: float, x: float) -> float:
    """Continued fraction for the regularised incomplete beta (modified Lentz)."""
    tiny = 1e-300
    c, d = 1.0, 1.0 - (a + b) * x / (a + 1)
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 1000):
        for num in (m * (b - m) * x / ((a + 2 * m - 1) * (a + 2 * m)), -(a + m) * (a + b + m) * x / ((a + 2 * m) * (a + 2 * m + 1))):
            d = 1.0 + num * d
            d = 1.0 / (d if abs(d) > tiny else tiny)
            c = 1.0 + num / c
            c = c if abs(c) > tiny else tiny
            h *= d * c
        if abs(d * c - 1.0) < 1e-15:
            return h
    raise ArithmeticError("incomplete beta did not converge")


def _beta_inc(a: float, b: float, x: float) -> float:
    """Regularised incomplete beta I_x(a, b)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    log_front = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    if x < (a + 1) / (a + b + 2):
        return math.exp(log_front) * _beta_cf(a, b, x) / a
    return 1.0 - math.exp(log_front) * _beta_cf(b, a, 1 - x) / b


def _f_power(df1: int, df2: int, noncentrality: float, alpha: float) -> float:
    """Power of an F test: P(F' > F_crit) for noncentral F(df1, df2, lambda).

    Works on y = df1 F / (df1 F + df2), where the central F is Beta(df1/2, df2/2) and the noncentral one
    is a Poisson(lambda/2) mixture of Beta(df1/2 + j, df2/2)."""
    a, b = df1 / 2, df2 / 2
    lo, hi = 0.0, 1.0
    for _ in range(200):  # critical y by bisection: I_y(a, b) = 1 - alpha
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if _beta_inc(a, b, mid) < 1 - alpha else (lo, mid)
    y_crit = (lo + hi) / 2
    half = noncentrality / 2
    start = int(half)  # sum outward from the Poisson mode so large lambda stays stable
    below = 0.0
    total_weight = 0.0
    for direction in (range(start, -1, -1), range(start + 1, start + 100_000)):
        for j in direction:
            weight = math.exp(-half + j * math.log(half) - math.lgamma(j + 1)) if half > 0 else float(j == 0)
            total_weight += weight
            below += weight * _beta_inc(a + j, b, y_crit)
            if weight < 1e-16 and j != start:
                break
    return 1.0 - below / total_weight


def _smallest_n(power_at, n_min: int, power: float) -> int:
    """Smallest n >= n_min with power_at(n) >= power (power rises with n)."""
    hi = n_min
    while power_at(hi) < power:
        hi *= 2
        if hi > 10_000_000:
            raise ValueError("effect is too small: the sample size would exceed 10 million")
    lo = max(n_min, hi // 2)
    while lo < hi:
        mid = (lo + hi) // 2
        lo, hi = (mid + 1, hi) if power_at(mid) < power else (lo, mid)
    return lo


def anova_oneway(f: float, groups: int, alpha: float = 0.05, power: float = 0.8) -> int:
    """Participants per group for a one-way fixed-effects ANOVA with Cohen's f and k equal groups.

    df1 = k - 1, df2 = N - k, lambda = f^2 N, N = k n."""
    if f <= 0:
        raise ValueError("f must be greater than 0")
    if groups < 2:
        raise ValueError("groups must be at least 2")
    _check_unit("alpha", alpha)
    _check_unit("power", power)
    return _smallest_n(lambda n: _f_power(groups - 1, groups * n - groups, f**2 * groups * n, alpha), 2, power)


def regression(f2: float, predictors: int, alpha: float = 0.05, power: float = 0.8) -> int:
    """Total participants for the overall F test of R^2 = 0 in multiple regression, Cohen's f^2 and u predictors.

    df1 = u, df2 = N - u - 1, lambda = f^2 N."""
    if f2 <= 0:
        raise ValueError("f2 must be greater than 0")
    if predictors < 1:
        raise ValueError("predictors must be at least 1")
    _check_unit("alpha", alpha)
    _check_unit("power", power)
    return _smallest_n(lambda n: _f_power(predictors, n - predictors - 1, f2 * n, alpha), predictors + 2, power)


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
        "anova_oneway": ("f", "groups"),
        "regression": ("f2", "predictors"),
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
        "anova_oneway": anova_oneway,
        "regression": regression,
    }[test]
    used = {k: v for k, v in params.items() if v is not None}
    n = fn(**used)
    groups = 2 if test in ("two_means", "two_proportions") else used["groups"] if test == "anova_oneway" else 1
    recruit = _inflate(n, attrition)
    unit = "per group" if groups > 1 else "in total"
    return {
        "test": test,
        "inputs": used,
        "attrition": attrition,
        "n_analysable": n,
        "n_to_recruit": recruit,
        "unit": unit,
        "total_to_recruit": recruit * groups,
        "note": EXACT_NOTE if test in ("anova_oneway", "regression") else NOTE,
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
