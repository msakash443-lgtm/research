import pytest

from app.agreement import cohen_kappa
from app.ai_quality import (
    CONFIRMED,
    NOT_ELIGIBLE,
    OVERTURNED,
    PENDING,
    TRUSTED,
    autoexclude_status,
    counts_as_excluded,
    due_for_sample,
    is_autoexclude_eligible,
    sample_interval,
    should_disable_prescreen,
)


def test_only_a_high_confidence_exclude_is_eligible():
    assert is_autoexclude_eligible("exclude", 0.95, 0.9) is True
    assert is_autoexclude_eligible("exclude", 0.89, 0.9) is False
    assert is_autoexclude_eligible("include", 0.99, 0.9) is False
    assert is_autoexclude_eligible("exclude", None, 0.9) is False


def test_sample_interval_is_the_rounded_reciprocal_of_the_rate():
    assert sample_interval(0.10) == 10
    assert sample_interval(0.25) == 4
    assert sample_interval(1.0) == 1


def test_sample_interval_rejects_an_impossible_rate():
    with pytest.raises(ValueError):
        sample_interval(0)
    with pytest.raises(ValueError):
        sample_interval(1.5)


def test_every_tenth_eligible_item_is_sampled():
    sampled = [n for n in range(1, 31) if due_for_sample(n, 0.10)]
    assert sampled == [10, 20, 30]


def test_autoexclude_status_transitions():
    assert autoexclude_status(eligible=False, audit_status=None) == NOT_ELIGIBLE
    assert autoexclude_status(eligible=True, audit_status=None) == TRUSTED
    assert autoexclude_status(eligible=True, audit_status="pending") == PENDING
    assert autoexclude_status(eligible=True, audit_status="confirmed") == CONFIRMED
    assert autoexclude_status(eligible=True, audit_status="overturned") == OVERTURNED


def test_only_trusted_and_confirmed_count_as_excluded():
    assert counts_as_excluded(TRUSTED) is True
    assert counts_as_excluded(CONFIRMED) is True
    assert counts_as_excluded(PENDING) is False
    assert counts_as_excluded(OVERTURNED) is False
    assert counts_as_excluded(NOT_ELIGIBLE) is False


def test_should_disable_prescreen_needs_enough_pairs_and_a_defined_low_kappa():
    low_kappa = cohen_kappa([("exclude", "include")] * 25)  # systematic disagreement
    assert should_disable_prescreen(low_kappa, min_n=20, min_kappa=0.4) is True


def test_should_disable_prescreen_ignores_too_few_pairs():
    low_kappa = cohen_kappa([("exclude", "include")] * 5)
    assert should_disable_prescreen(low_kappa, min_n=20, min_kappa=0.4) is False


def test_should_disable_prescreen_ignores_an_undefined_kappa():
    one_label = cohen_kappa([("exclude", "exclude")] * 40)  # kappa undefined, not evidence of disagreement
    assert should_disable_prescreen(one_label, min_n=20, min_kappa=0.4) is False


def test_should_disable_prescreen_leaves_good_agreement_alone():
    good = cohen_kappa([("exclude", "exclude")] * 20 + [("include", "include")] * 20)
    assert should_disable_prescreen(good, min_n=20, min_kappa=0.4) is False
