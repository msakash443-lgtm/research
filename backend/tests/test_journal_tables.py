import pytest
import statistics

from app import journal_tables
from app.journal_tables import TableError

OUTPUT = {
    "descriptives": {
        "age": {"n": 4, "mean": 30.126, "sd": 5.4321, "min": 22.0, "max": 40.0},
        "score": {"n": 4, "mean": 0.5, "sd": None, "min": 0.1, "max": 0.9},
    },
    "correlations": [
        {"a": "age", "b": "score", "r": -0.456},
        {"a": "age", "b": "iq", "r": 0.05},
        {"a": "score", "b": "iq", "r": -0.001},
    ],
}


def test_descriptives_rows_are_copied_and_rounded_for_display():
    table = journal_tables.build_tables(OUTPUT)["descriptives"]
    assert table["columns"] == ["Variable", "n", "M", "SD", "Min", "Max"]
    assert table["rows"][0] == ["age", "4", "30.13", "5.43", "22.00", "40.00"]
    # one observation has no SD: shown as a dash, never as 0
    assert table["rows"][1][3] == journal_tables.NO_VALUE


def test_correlation_matrix_is_lower_triangular_without_leading_zero():
    table = journal_tables.build_tables(OUTPUT)["correlations"]
    assert table["columns"] == ["Variable", "age", "score", "iq"]
    assert table["rows"][0] == ["age", "—", "", ""]
    assert table["rows"][1] == ["score", "-.46", "—", ""]
    # r = -0.001 rounds to zero and must not print as a negative result
    assert table["rows"][2] == ["iq", ".05", ".00", "—"]


def test_only_tests_present_in_the_output_are_tabulated():
    assert list(journal_tables.build_tables({"descriptives": OUTPUT["descriptives"]})) == ["descriptives"]


@pytest.mark.parametrize(
    "bad",
    [
        None,
        {},
        {"seed": 1},
        {"descriptives": {}},
        {"descriptives": {"x": {"n": 1, "mean": float("nan"), "sd": None, "min": 1, "max": 1}}},
        {"descriptives": {"x": {"n": True, "mean": 1, "sd": None, "min": 1, "max": 1}}},
        {"descriptives": {"x": {"n": 2, "mean": 1}}},
        {"correlations": []},
        {"correlations": [{"a": "x", "b": "y", "r": 1.5}]},
        {"correlations": [{"a": "x", "r": 0.2}]},
    ],
)
def test_malformed_output_is_refused(bad):
    with pytest.raises(TableError):
        journal_tables.build_tables(bad)


def test_a_perfect_correlation_with_float_rounding_overshoot_is_accepted_and_shown_as_one():
    # statistics.correlation returns 1.0000000000000002 for these exactly collinear columns.
    r = statistics.correlation([1, 1, 2], [8, 8, 15])
    assert r > 1.0
    table = journal_tables.correlation_table([{"a": "x", "b": "y", "r": r}])
    assert table["rows"][1] == ["y", "1.00", "—"]


def test_a_perfect_negative_correlation_with_float_rounding_overshoot_is_accepted_and_shown_as_minus_one():
    # statistics.correlation returns -1.0000000000000002 for these exactly collinear columns.
    r = statistics.correlation([1, 1, 2], [-8, -8, -15])
    assert r < -1.0
    table = journal_tables.correlation_table([{"a": "x", "b": "y", "r": r}])
    assert table["rows"][1] == ["y", "-1.00", "—"]


def test_a_correlation_clearly_outside_minus_one_to_one_is_still_refused():
    with pytest.raises(journal_tables.TableError):
        journal_tables.correlation_table([{"a": "x", "b": "y", "r": 1.0001}])
    with pytest.raises(journal_tables.TableError):
        journal_tables.correlation_table([{"a": "x", "b": "y", "r": -1.0001}])
