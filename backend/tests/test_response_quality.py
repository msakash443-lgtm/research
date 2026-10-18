import pytest

from app import response_quality as rq

COLS = ["response_id", "created_at", "submitted_at", "q1", "q2", "q3", "q4", "q5", "check"]
GRID = ["q1", "q2", "q3", "q4", "q5"]


def _row(rid, start="2026-10-01T10:00:00+00:00", end="2026-10-01T10:10:00+00:00",
         answers=("1", "2", "3", "4", "5"), check="pick 3"):
    return [rid, start, end, *answers, check]


def test_clean_rows_are_not_flagged():
    rows = [_row("r1", answers=("1", "2", "3", "4", "5")), _row("r2", answers=("5", "1", "2", "3", "4"))]
    report = rq.check(COLS, rows, attention={"check": "pick 3"}, grid_columns=GRID, start_column="created_at")
    assert report["flagged_count"] == 0 and report["removes_rows"] is False


def test_attention_failure_is_flagged_by_row():
    rows = [_row("r1", check="pick 1")]
    report = rq.check(COLS, rows, attention={"check": "pick 3"})
    assert report["flags"] == [{"response_id": "r1", "row": 1, "reasons": ["attention_failed:check"]}]
    assert report["summary"]["attention_failed"] == 1


def test_straight_lining_runs_only_over_named_grid_columns():
    rows = [_row("r1", answers=("3", "3", "3", "3", "3")), _row("r2", answers=("3", "3", "3", "3", ""))]
    report = rq.check(COLS, rows, grid_columns=GRID)  # default minimum is 5 grid answers
    assert [f["response_id"] for f in report["flags"]] == ["r1"]
    lowered = rq.check(COLS, rows, grid_columns=GRID, straightline_min_items=4)
    assert {f["response_id"] for f in lowered["flags"]} == {"r1", "r2"}


def test_straight_lining_ignores_columns_that_are_not_grid_items():
    # Age-like and free-text columns are identical here, but they are not grid items, so no flag.
    columns = ["response_id", "created_at", "submitted_at", "age", "q1", "q2", "q3", "q4", "q5", "comment"]
    row = ["r1", "2026-10-01T10:00:00+00:00", "2026-10-01T10:10:00+00:00", "34", "1", "2", "4", "5", "3", "same"]
    assert rq.check(columns, [row], grid_columns=["q1", "q2"])["flags"] == []
    grid_only = rq.check(columns, [["r1", "", "", "34", "3", "3", "3", "3", "3", "same"]], grid_columns=["q1", "q2", "q3", "q4", "q5"])
    assert grid_only["flags"] == [{"response_id": "r1", "row": 1, "reasons": ["straight_lined"]}]


def test_straight_lining_is_not_run_without_grid_columns():
    rows = [_row("r1", answers=("3", "3", "3", "3", "3"))]
    report = rq.check(COLS, rows)
    assert report["flags"] == []
    assert report["checks"] == {"straight_lining": False, "speeder": False}


def test_speeders_compare_start_to_submit_when_a_start_column_is_named():
    rows = [_row("fast", end="2026-10-01T10:00:20+00:00"), _row("slow", answers=("2", "4", "1", "5", "3"))]
    report = rq.check(COLS, rows, min_seconds=60, start_column="created_at")
    assert report["flags"] == [{"response_id": "fast", "row": 1, "reasons": ["speeder"]}]
    assert report["checks"]["speeder"] is True


def test_speeder_is_not_run_without_a_real_start_time():
    # Forms has no start time: createTime equals lastSubmittedTime on unedited responses, so the check must not run.
    same = _row("unedited", start="2026-10-01T10:00:00+00:00", end="2026-10-01T10:00:00+00:00")
    report = rq.check(COLS, [same], min_seconds=60)
    assert report["flags"] == []
    assert report["checks"]["speeder"] is False


def test_duplicates_flag_later_copies_only_and_ignore_ids_and_times():
    rows = [
        _row("r1", answers=("1", "2", "3", "4", "5")),
        _row("r2", start="2026-10-01T09:50:00+00:00", answers=("1", "2", "3", "4", "5")),
    ]
    report = rq.check(COLS, rows)
    assert report["flags"] == [{"response_id": "r2", "row": 2, "reasons": ["duplicate_answers"]}]


def test_unknown_or_invalid_column_arguments_are_refused():
    with pytest.raises(ValueError):
        rq.check(COLS, [], attention={"nope": "x"})
    with pytest.raises(ValueError):
        rq.check(["a", "a"], [])
    with pytest.raises(ValueError):
        rq.check(COLS, [], straightline_min_items=1)
    with pytest.raises(ValueError):
        rq.check(COLS, [], min_seconds=-1)
    with pytest.raises(ValueError, match="Grid column not found"):
        rq.check(COLS, [], grid_columns=["nope"])
    with pytest.raises(ValueError, match="plain question column"):
        rq.check(COLS, [], grid_columns=["check"], attention={"check": "pick 3"})
    with pytest.raises(ValueError, match="Start-time column not found"):
        rq.check(COLS, [], start_column="nope")


def test_submit_before_start_is_flagged_as_invalid_time_not_a_speeder():
    report = rq.check(COLS, [_row("r", start="2026-10-01T10:00:00+00:00", end="2026-10-01T09:00:00+00:00")],
                      min_seconds=60, start_column="created_at")
    assert report["flags"] == [{"response_id": "r", "row": 1, "reasons": ["time_order_invalid"]}]
    assert report["summary"]["time_order_invalid"] == 1


def test_unparseable_times_are_not_guessed_as_speeders():
    report = rq.check(COLS, [_row("r", start="not-a-time", end="also-bad")], min_seconds=60, start_column="created_at")
    assert report["flags"] == []


def test_mixed_naive_and_offset_aware_timestamps_are_refused_as_value_error():
    # Refused with ValueError (the route turns it into a 422), never an unhandled TypeError.
    row = _row("r", start="2026-10-01T10:00:00", end="2026-10-01T10:10:00+00:00")
    with pytest.raises(ValueError, match="mix a time zone"):
        rq.check(COLS, [row], start_column="created_at")
