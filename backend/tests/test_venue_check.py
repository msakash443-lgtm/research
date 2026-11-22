"""Venue check against the owner-supplied predatory list (X.201)."""

import json
import uuid

from app.connectors.crossref import to_record
from app.venue_check import ListedVenue, match_venue, normalise_issn, source_issns
from tests.test_citation_guard import add_source, automatic_check, research, services, world  # noqa: F401

LIST = "https://lists.example/predatory-2026"


def put_issn(sid, issn="1234-5678"):
    from app.database import SessionLocal
    from app.models import Source
    with SessionLocal() as db:
        db.get(Source, uuid.UUID(sid)).source_ids = {"issn": issn}
        db.commit()


def supply_list(c, pid, **over):
    row = {"name": "Journal of Doubtful Results", "source_url": LIST, "checked_at": "2026-11-01", "issns": ["1234-5678"], **over}
    assert c.post(f"/api/projects/{pid}/journals/official-list", json=[row]).status_code == 201


def test_an_issn_matches_in_any_spelling_and_a_venue_name_only_matches_whole():
    listed = [ListedVenue("Journal of Doubtful Results", ("1234567x",), LIST)]
    assert normalise_issn("1234567x") == "1234-567X"
    assert match_venue(None, source_issns({"issn": ["1234-567X"]}), listed) == {
        "list": LIST, "journal": "Journal of Doubtful Results", "reason": "issn", "issn": "1234-567X"}
    assert match_venue("journal of doubtful results.", set(), listed)["reason"] == "venue_name"
    assert match_venue("Journal of Doubtful Results Letters", set(), listed) is None
    assert match_venue(None, set(), listed) is None


def test_crossref_issn_is_kept_with_the_record_ids():
    record = to_record({"DOI": "10.1000/p1", "title": ["T"], "ISSN": ["not-an-issn", "1234-5678"]})
    assert record.source_ids == {"crossref": "10.1000/p1", "issn": "1234-5678"}


def test_a_source_whose_issn_is_on_the_supplied_list_gets_the_flag_and_is_still_citable(world, services, fake_llm):
    c, pid = world
    sid = add_source(c, pid)
    put_issn(sid)
    supply_list(c, pid)

    checked = automatic_check(c, pid, sid)

    assert "predatory_venue_match" in checked["quality_flags"]
    assert checked["verification"]["venue_check"] == {
        "list": LIST, "journal": "Journal of Doubtful Results", "reason": "issn", "issn": "1234-5678"}
    run = research(c, pid)
    assert run["status"] == "completed"  # a warning, not a block
    user = json.loads(fake_llm.requests[0].content)["messages"][1]["content"]
    assert "predatory-journal list" in user


def test_with_no_list_configured_there_is_no_flag_and_no_error(world, services):
    c, pid = world
    sid = add_source(c, pid)
    put_issn(sid)

    checked = automatic_check(c, pid, sid)

    assert "predatory_venue_match" not in (checked["quality_flags"] or [])
    assert checked["verification"]["venue_check"] is None
