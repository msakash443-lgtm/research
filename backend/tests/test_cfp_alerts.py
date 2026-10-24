from datetime import date

import pytest

from app.cfp_alerts import classify_opportunities


def test_cfp_alerts_classify_and_sort_supplied_deadlines_without_fetching():
    rows = classify_opportunities(
        [
            {"name": "Later CFP", "deadline": "2026-12-01", "source_url": "https://example/later"},
            {"name": "Past CFP", "deadline": "2026-01-10", "source_url": "https://example/past"},
            {"name": "Today CFP", "deadline": "2026-06-15", "source_url": "https://example/today"},
        ],
        today=date(2026, 6, 15),
    )

    assert [row["name"] for row in rows] == ["Past CFP", "Today CFP", "Later CFP"]
    assert [row["status"] for row in rows] == ["expired", "upcoming", "upcoming"]
    assert rows[1]["source_url"] == "https://example/today"


def test_cfp_alerts_reject_missing_or_malformed_deadlines():
    with pytest.raises(ValueError, match="name, deadline, and source_url"):
        classify_opportunities([{"name": "CFP", "deadline": "2026-06-15"}])
    with pytest.raises(ValueError, match="expected YYYY-MM-DD"):
        classify_opportunities([{"name": "CFP", "deadline": "15/06/2026", "source_url": "https://example"}])


def test_cfp_alerts_keep_duplicate_names_distinct_by_source():
    rows = classify_opportunities(
        [
            {"name": "Same CFP", "deadline": "2026-08-01", "source_url": "https://example/b"},
            {"name": "Same CFP", "deadline": "2026-08-01", "source_url": "https://example/a"},
        ],
        today=date(2026, 1, 1),
    )
    assert [row["source_url"] for row in rows] == ["https://example/a", "https://example/b"]
