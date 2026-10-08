"""Plan X.5: evaluation metrics. The labels here are made-up test inputs, not gold data."""

import pytest

from app.evaluation import (
    FAILED, INCOMPLETE, PASSED, AiScreen, dataset_hash, extraction_eval, same_value, screening_eval,
)


def _gold(includes, excludes):
    return {**{f"i{n}": "include" for n in range(includes)}, **{f"e{n}": "exclude" for n in range(excludes)}}


def _ai_agree(gold):
    return {k: AiScreen(decision=v) for k, v in gold.items()}


# ---- screening ------------------------------------------------------------------------------------------

def test_perfect_agreement_passes():
    gold = _gold(20, 30)
    result = screening_eval(gold, _ai_agree(gold))
    assert result.status == PASSED and result.kappa == pytest.approx(1.0) and result.false_exclusion_rate == 0


def test_one_false_exclusion_in_twenty_includes_fails_even_with_high_kappa():
    gold = _gold(20, 80)
    ai = _ai_agree(gold)
    ai["i0"] = AiScreen(decision="exclude")  # 1/20 = 5% false exclusions
    result = screening_eval(gold, ai)
    assert result.false_exclusions == ["i0"] and result.false_exclusion_rate == pytest.approx(0.05)
    assert result.kappa > 0.8 and result.status == FAILED


def test_low_kappa_fails():
    gold = _gold(10, 10)
    ai = {k: AiScreen(decision="include") for k in gold}  # says include to everything: no false exclusions
    result = screening_eval(gold, ai)
    assert result.false_exclusion_rate == 0 and result.status in (FAILED, INCOMPLETE)
    assert result.kappa is None or result.kappa < 0.8


def test_maybe_is_not_a_false_exclusion_and_is_reported_beside_kappa():
    gold = _gold(10, 10)
    ai = _ai_agree(gold)
    for key in ("i0", "i1", "e0"):
        ai[key] = AiScreen(decision="maybe")
    result = screening_eval(gold, ai)
    assert result.false_exclusions == [] and result.maybe == 3 and result.maybe_rate == pytest.approx(3 / 20)
    assert result.decided == 17 and result.status == PASSED


def test_any_ai_failure_makes_the_result_incomplete_not_passed():
    gold = _gold(20, 30)
    ai = _ai_agree(gold)
    ai["e3"] = AiScreen(error="schema violation")
    del ai["e4"]  # never answered
    result = screening_eval(gold, ai)
    assert result.failed == 2 and result.status == INCOMPLETE
    assert any("failed on 2 of 50" in p for p in result.problems)


def test_no_gold_includes_means_the_false_exclusion_rate_is_undefined():
    gold = _gold(0, 10)
    result = screening_eval(gold, _ai_agree(gold))
    assert result.false_exclusion_rate is None and result.status == INCOMPLETE


def test_an_empty_gold_set_is_incomplete():
    result = screening_eval({}, {})
    assert result.status == INCOMPLETE and "no papers" in result.problems[0]


def test_answers_for_unknown_papers_or_bad_labels_are_refused():
    with pytest.raises(ValueError):
        screening_eval({"a": "include"}, {"zzz": AiScreen(decision="include")})
    with pytest.raises(ValueError):
        screening_eval({"a": "maybe"}, {})
    with pytest.raises(ValueError):
        screening_eval({"a": "include"}, {"a": AiScreen(decision="yes")})


# ---- extraction -----------------------------------------------------------------------------------------

GOLD_X = {
    "p1": {"sample_size": 120, "country": "India", "effect": None},
    "p2": {"sample_size": 80, "country": "Kenya", "effect": 0.3},
}


def test_exact_extraction_passes():
    ai = {"p1": {"sample_size": "120", "country": " india ", "effect": None}, "p2": {"sample_size": 80.0, "country": "Kenya", "effect": 0.3}}
    result = extraction_eval(GOLD_X, ai, critical=["sample_size", "effect"])
    assert result.status == PASSED
    assert result.fields["sample_size"].precision == 1 and result.fields["effect"].recall == 1


def test_a_wrong_value_is_both_a_false_positive_and_a_false_negative():
    ai = {"p1": {"sample_size": 121, "country": "India", "effect": None}, "p2": {"sample_size": 80, "country": "Kenya", "effect": 0.3}}
    s = extraction_eval(GOLD_X, ai, critical=["sample_size"]).fields["sample_size"]
    assert (s.tp, s.fp, s.fn) == (1, 1, 1) and s.precision == 0.5


def test_a_value_where_the_paper_has_none_is_a_false_positive():
    ai = {"p1": {"sample_size": 120, "country": "India", "effect": 0.9}, "p2": {"sample_size": 80, "country": "Kenya", "effect": 0.3}}
    result = extraction_eval(GOLD_X, ai, critical=["effect"])
    assert (result.fields["effect"].tp, result.fields["effect"].fp) == (1, 1) and result.status == FAILED


def test_a_missed_value_lowers_recall():
    ai = {"p1": {"sample_size": None}, "p2": {"sample_size": 80}}
    s = extraction_eval(GOLD_X, ai, critical=["sample_size"]).fields["sample_size"]
    assert (s.tp, s.fn, s.recall) == (1, 1, 0.5)


def test_a_failed_paper_makes_extraction_incomplete():
    result = extraction_eval(GOLD_X, {"p1": {"sample_size": 120}}, critical=["sample_size"])
    assert result.failed == ["p2"] and result.status == INCOMPLETE


def test_a_critical_field_with_nothing_to_score_is_incomplete():
    result = extraction_eval(GOLD_X, {"p1": {}, "p2": {}}, critical=["design"])
    assert result.status == INCOMPLETE and any("'design'" in p for p in result.problems)


def test_naming_no_critical_fields_is_incomplete():
    result = extraction_eval(GOLD_X, {"p1": GOLD_X["p1"], "p2": GOLD_X["p2"]}, critical=[])
    assert result.status == INCOMPLETE


@pytest.mark.parametrize(
    "a,b,same",
    [("120", 120, True), ("0.30", 0.3, True), (" Mixed  Methods", "mixed methods", True), ("RCT", "rct ", True),
     (True, "true", False), (1, True, False), (["A", "b"], ["a", "B"], True), ("12", "120", False), ("nan", "nan", True)],
)
def test_value_comparison_is_normalised_but_never_fuzzy(a, b, same):
    assert same_value(a, b) is same


def test_dataset_hash_is_stable_and_content_sensitive():
    assert dataset_hash({"a": 1, "b": 2}) == dataset_hash({"b": 2, "a": 1})
    assert dataset_hash({"a": 1}) != dataset_hash({"a": 2})
