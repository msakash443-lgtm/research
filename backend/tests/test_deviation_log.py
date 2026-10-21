import pytest

from app import deviation_log
from app.deviation_log import DeviationError

PLANNED = {"tests": ["descriptives", "correlations"], "variables": ["age", "score"], "seed": 1}


def keys(actual, ds="h1"):
    return [d.key for d in deviation_log.find_deviations(PLANNED, actual, "h1", ds)]


def test_identical_plan_has_no_deviations():
    assert deviation_log.build_log(PLANNED, dict(PLANNED), "h1", "h1", {}) == []


def test_every_kind_of_deviation_is_listed_in_a_fixed_order():
    actual = {"tests": ["descriptives"], "variables": ["age", "iq"], "seed": 2}
    assert keys(actual, "h2") == [
        "test_removed:correlations",
        "variable_removed:score",
        "variable_added:iq",
        "seed_changed",
        "dataset_changed",
    ]


def test_log_refuses_a_deviation_with_no_reason():
    actual = {**PLANNED, "variables": ["age"]}
    with pytest.raises(DeviationError, match="variable_removed:score"):
        deviation_log.build_log(PLANNED, actual, "h1", "h1", {})
    with pytest.raises(DeviationError):
        deviation_log.build_log(PLANNED, actual, "h1", "h1", {"variable_removed:score": "   "})


def test_reason_is_attached_and_whitespace_collapsed():
    actual = {**PLANNED, "seed": 9}
    log = deviation_log.build_log(PLANNED, actual, "h1", "h1", {"seed_changed": "  fixed  a\nbug "})
    assert [(d.key, d.reason) for d in log] == [("seed_changed", "fixed a bug")]


def test_reason_for_a_non_deviation_is_refused():
    with pytest.raises(DeviationError, match="seed_changd"):
        deviation_log.build_log(PLANNED, {**PLANNED, "seed": 9}, "h1", "h1", {"seed_changed": "x", "seed_changd": "y"})


def test_overlong_reason_and_malformed_spec_are_refused():
    with pytest.raises(DeviationError):
        deviation_log.build_log(PLANNED, {**PLANNED, "seed": 9}, "h1", "h1", {"seed_changed": "x" * 2001})
    with pytest.raises(DeviationError):
        deviation_log.find_deviations(PLANNED, {"seed": 1}, "h1", "h1")
