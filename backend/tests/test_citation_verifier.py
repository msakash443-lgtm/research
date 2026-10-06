import pytest

from app.citation_verifier import Reference, Verdict, verify_citation, verify_citations
from app.connectors.base import ConnectorBase, ConnectorError, NotSupportedError, PaperRecord, SearchPage, SearchRequest

TITLE = "Remote work and worker productivity: evidence from a field experiment"
AUTHORS = ("Ada Lovelace", "Grace Hopper", "Alan Turing")
DOI = "10.1234/rw.2020"


def record(connector="crossref", ext="1", title=TITLE, authors=AUTHORS, year=2020, doi=DOI, flags=()):
    return PaperRecord(connector=connector, external_id=ext, title=title, authors=tuple(authors), year=year, doi=doi, quality_flags=tuple(flags))


def ref(**over):
    base = dict(title=TITLE, authors=AUTHORS, year=2020, doi=DOI)
    base.update(over)
    return Reference(**base)


class Fake(ConnectorBase):
    """by_doi: {doi: record}; searches: list of records returned by search. error/doi_unsupported switch behaviour."""

    def __init__(self, name="crossref", by_doi=None, searches=(), error=None, doi_unsupported=False, search_unsupported=False):
        self.name, self.by_doi, self.searches = name, by_doi or {}, list(searches)
        self.error, self.doi_unsupported, self.search_unsupported = error, doi_unsupported, search_unsupported
        self.calls = []

    def get_by_id(self, external_id):
        self.calls.append(("id", external_id))
        if self.doi_unsupported:
            raise NotSupportedError("no doi lookup")
        if self.error:
            raise ConnectorError(self.error)
        return self.by_doi.get(external_id)

    def search(self, request: SearchRequest):
        self.calls.append(("search", request.query))
        if self.search_unsupported:
            raise NotSupportedError("no search")
        if self.error:
            raise ConnectorError(self.error)
        return SearchPage(records=tuple(self.searches))


def test_a_real_reference_is_verified_by_doi():
    conn = Fake(by_doi={DOI: record()})
    result = verify_citation(ref(), [conn])
    assert result.verdict is Verdict.verified and result.matched.external_id == "1"
    assert all(result.checks.values()) and result.consulted == ("crossref",) and result.errors == ()
    assert conn.calls == [("id", DOI)]  # no needless search once confirmed


def test_verified_by_title_search_when_there_is_no_doi():
    conn = Fake(searches=[record(title="Something else entirely about fish", ext="9"), record(ext="2", doi=None)])
    result = verify_citation(ref(doi=None), [conn])
    assert result.verdict is Verdict.verified and result.matched.external_id == "2"


def test_small_title_differences_are_tolerated_but_not_different_titles():
    near = record(title="Remote Work and Worker Productivity - Evidence from a Field Experiment")
    assert verify_citation(ref(), [Fake(by_doi={DOI: near})]).verdict is Verdict.verified
    other = record(title="Hybrid schedules and employee wellbeing in large firms")
    result = verify_citation(ref(), [Fake(by_doi={DOI: other})])
    assert result.verdict is Verdict.mismatch and "The title differs" in result.reasons


def test_real_doi_with_invented_details_is_a_mismatch_naming_the_fields():
    wrong = record(authors=("Jane Roe", "John Doe"), year=2018)
    result = verify_citation(ref(), [Fake(by_doi={DOI: wrong})])
    assert result.verdict is Verdict.mismatch
    assert result.checks["title"] is True and result.checks["authors"] is False and result.checks["year"] is False
    assert any("authors differ" in r for r in result.reasons) and any("year differs" in r for r in result.reasons)


def test_a_year_off_by_one_is_still_a_mismatch():
    result = verify_citation(ref(), [Fake(by_doi={DOI: record(year=2021)})])
    assert result.verdict is Verdict.mismatch and result.checks == {"title": True, "authors": True, "year": False, "doi": True}


def test_author_rules_first_author_and_overlap():
    # reference may list fewer authors than the record (et al.), but the first must agree
    assert verify_citation(ref(authors=("Lovelace, Ada",)), [Fake(by_doi={DOI: record()})]).verdict is Verdict.verified
    swapped = ("Grace Hopper", "Ada Lovelace", "Alan Turing")
    assert verify_citation(ref(authors=AUTHORS), [Fake(by_doi={DOI: record(authors=swapped)})]).verdict is Verdict.mismatch
    one_in_four = ("Ada Lovelace", "X One", "Y Two", "Z Three")
    assert verify_citation(ref(authors=one_in_four), [Fake(by_doi={DOI: record()})]).verdict is Verdict.mismatch


def test_a_record_with_a_different_doi_does_not_verify_the_reference():
    other = record(doi="10.9999/other")
    result = verify_citation(ref(), [Fake(by_doi={DOI: other})])
    assert result.verdict is Verdict.mismatch and "The record has a different DOI" in result.reasons


def test_a_miss_is_not_found_not_fabricated():
    result = verify_citation(ref(), [Fake(), Fake(name="semantic_scholar")])
    assert result.verdict is Verdict.not_found and result.consulted == ("crossref", "semantic_scholar")
    assert "not" in result.reasons[0] and "fabricated" in result.reasons[0] and result.matched is None


def test_one_service_missing_it_does_not_stop_another_confirming_it():
    result = verify_citation(ref(), [Fake(name="openalex"), Fake(name="semantic_scholar", by_doi={DOI: record("semantic_scholar", "S1")})])
    assert result.verdict is Verdict.verified and result.matched.connector == "semantic_scholar"


def test_failures_are_unavailable_not_not_found_and_never_raise():
    result = verify_citation(ref(), [Fake(error="HTTP 503"), Fake(name="openalex", error="timeout")])
    assert result.verdict is Verdict.unavailable and len(result.errors) == 2 and result.consulted == ()
    partial = verify_citation(ref(), [Fake(error="HTTP 503"), Fake(name="openalex")])
    assert partial.verdict is Verdict.unavailable  # one service said no, one broke: can't conclude
    assert verify_citation(ref(), []).verdict is Verdict.unavailable


def test_a_failure_does_not_hide_a_confirmation_elsewhere():
    result = verify_citation(ref(), [Fake(error="HTTP 503"), Fake(name="openalex", by_doi={DOI: record("openalex", "W1")})])
    assert result.verdict is Verdict.verified and len(result.errors) == 1


def test_services_without_doi_lookup_are_skipped_without_error():
    conn = Fake(name="openalex", doi_unsupported=True, searches=[record("openalex", "W5")])
    result = verify_citation(ref(), [conn])
    assert result.verdict is Verdict.verified and result.errors == ()
    value_error = Fake(name="openalex", searches=[record("openalex", "W5")])
    value_error.get_by_id = lambda external_id: (_ for _ in ()).throw(ValueError("not an OpenAlex id"))
    assert verify_citation(ref(), [value_error]).verdict is Verdict.verified


def test_title_search_hits_with_other_titles_are_not_a_mismatch():
    conn = Fake(searches=[record(title="Completely unrelated fish farming study", ext="3")])
    assert verify_citation(ref(doi=None), [conn]).verdict is Verdict.not_found


def test_incomplete_references_are_not_checked_over_the_network():
    conn = Fake(by_doi={DOI: record()})
    for bad in (ref(title=None), ref(title="  "), ref(authors=()), ref(year=None)):
        result = verify_citation(bad, [conn])
        assert result.verdict is Verdict.incomplete
    assert conn.calls == []


def test_retracted_is_reported_alongside_verification():
    result = verify_citation(ref(), [Fake(by_doi={DOI: record(flags=("retracted", "preprint"))})])
    assert result.verdict is Verdict.verified and result.flags == ("retracted",)


def test_summary_has_ids_and_verdicts_but_no_text():
    result = verify_citation(ref(), [Fake(by_doi={DOI: record()}), Fake(name="openalex", error="secret detail")])
    summary = result.summary()
    assert summary["verdict"] == "verified" and summary["matched"] == "crossref:1"
    assert "Remote work" not in str(summary) and "Lovelace" not in str(summary) and "secret detail" not in str(summary)


def test_batch_keeps_order():
    results = verify_citations([ref(), ref(year=None)], [Fake(by_doi={DOI: record()})])
    assert [r.verdict for r in results] == [Verdict.verified, Verdict.incomplete]


def test_the_verifier_never_touches_the_database_or_marks_anything_verified():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "app" / "citation_verifier.py").read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(("#", '"""')))
    assert "sqlalchemy" not in code and "app.models" not in code and "metadata_verified =" not in code
