"""M1.14.1: the same search run twice gives identical counts and a diffable log."""

import json

from sqlalchemy import select

from app.models import AuditEvent
from app.search_runner import diff_runs, rerun_search, run_search
from tests.test_search_runner import QUERY, FakeConnector, enabled, rec, setup  # noqa: F401  (enabled is an autouse fixture)


def _records():
    # a repeated external id and a same-DOI pair, so counts include dedupe removals
    return [rec(1), rec(2, doi="10.1234/x"), rec(3, doi="10.1234/x"), rec(4), rec(4), rec(5)]


def _log(row):
    """Everything a reviewer would diff, minus what legitimately differs between runs."""
    return json.dumps(
        {
            "database": row.database, "query_string": row.query_string, "filters": row.filters,
            "n_results": row.n_results, "counts": row.counts, "exact": row.exact,
            "caveats": row.caveats, "results": row.results,
        },
        sort_keys=True, indent=1,
    )


def test_same_search_twice_gives_identical_counts_and_an_empty_diff():
    db, project = setup()
    with db:
        first = run_search(db, project=project, actor="u", connector=FakeConnector(_records(), total=99), query=QUERY, filters={"year_from": 2015})
        second = rerun_search(db, project=project, actor="u", connector=FakeConnector(_records(), total=99), search_id=first.search_id)
        db.commit()

        assert (first.version, second.version) == (1, 2)
        assert first.counts == second.counts and first.counts["duplicates_removed"] > 0
        assert first.n_results == second.n_results
        assert first.query_string == second.query_string and first.filters == second.filters
        assert first.results == second.results
        assert diff_runs(first, second) == {"added": [], "removed": []}
        assert _log(first) == _log(second)  # byte-identical, so a text diff of two exports is empty

        counts = [
            e.payload_json["counts"]
            for e in db.scalars(select(AuditEvent).where(AuditEvent.action == "search.run").order_by(AuditEvent.id))
            if e.project_id == project.id
        ]
        assert len(counts) == 2 and counts[0] == counts[1]


def test_a_changed_source_shows_up_in_the_log_diff():
    db, project = setup()
    with db:
        first = run_search(db, project=project, actor="u", connector=FakeConnector(_records()), query=QUERY)
        second = rerun_search(db, project=project, actor="u", connector=FakeConnector(_records() + [rec(9)]), search_id=first.search_id)
        db.commit()
        assert _log(first) != _log(second)
        assert diff_runs(first, second) == {"added": ["openalex:W9"], "removed": []}
