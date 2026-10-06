import pytest

from app.connectors.base import PaperRecord
from app.dedupe import group_duplicates
from app.merge import MergeError, merge_records

T = "Female labour force participation in India"


def rec(connector="openalex", ext="W1", **over):
    base = dict(connector=connector, external_id=ext, title=T, doi="10.1000/abc", year=2020, authors=("Ada Lovelace",))
    base.update(over)
    return PaperRecord(**base)


def test_single_record_merges_to_itself_with_no_decisions():
    one = rec(abstract="An abstract.", source_ids={"openalex": "W1"})
    result = merge_records([one])
    assert result.record == one and result.decisions == () and result.members == ("openalex:W1",)


def test_every_members_id_is_kept_in_source_ids():
    a = rec("openalex", "W1", source_ids={"openalex": "W1", "pmid": "77"})
    b = rec("crossref", "10.1000/abc", source_ids={"crossref": "10.1000/abc"})
    c = rec("semantic_scholar", "a" * 40, source_ids={"semantic_scholar": "a" * 40, "arxiv": "2001.00001"})
    merged = merge_records([a, b, c]).record
    assert merged.source_ids == {
        "openalex": "W1", "pmid": "77", "crossref": "10.1000/abc", "semantic_scholar": "a" * 40, "arxiv": "2001.00001",
    }
    assert (merged.connector, merged.external_id) == ("openalex", "W1")  # the anchor stays the primary


def test_a_conflicting_id_under_the_same_key_is_logged_not_lost():
    result = merge_records([rec(ext="W1"), rec(ext="W2")])
    assert result.record.source_ids["openalex"] == "W1"
    (decision,) = [d for d in result.decisions if d.field == "source_ids.openalex"]
    assert decision.reason == "id_conflict" and "W2" in decision.passed_over[0]


def test_the_longest_abstract_wins_and_ties_stay_with_the_earlier_record():
    short, long_, tie = rec(ext="W1", abstract="Short."), rec("crossref", "c", abstract="A much longer and fuller abstract."), rec("arxiv", "x", abstract="Another tie....")
    result = merge_records([short, long_, tie])
    assert result.record.abstract == "A much longer and fuller abstract."
    decision = next(d for d in result.decisions if d.field == "abstract")
    assert decision.chosen_from == "crossref:c" and decision.reason == "longest"
    assert merge_records([rec(abstract="Same size."), rec("crossref", "c", abstract="Same size!")]).record.abstract == "Same size."


def test_an_abstract_is_supplied_by_whichever_member_has_one_and_flags_follow():
    bare = rec(quality_flags=("no_abstract",))
    full = rec("crossref", "c", abstract="Now there is one.")
    merged = merge_records([bare, full]).record
    assert merged.abstract == "Now there is one." and "no_abstract" not in merged.quality_flags
    assert "no_abstract" in merge_records([bare, rec("crossref", "c")]).record.quality_flags


def test_flags_are_unioned_and_invalid_doi_clears_once_a_valid_doi_is_known():
    a = rec(doi=None, quality_flags=("invalid_doi", "preprint"))
    b = rec("crossref", "c", doi="10.1000/abc", quality_flags=("retracted",))
    flags = merge_records([a, b]).record.quality_flags
    assert set(flags) == {"preprint", "retracted", "no_abstract"}
    assert "invalid_doi" in merge_records([a, rec("crossref", "c", doi=None)]).record.quality_flags


def test_longest_author_list_wins():
    merged = merge_records([rec(authors=("A. Lovelace",)), rec("crossref", "c", authors=("Ada Lovelace", "Charles Babbage"))]).record
    assert merged.authors == ("Ada Lovelace", "Charles Babbage")
    assert merge_records([rec(authors=()), rec("crossref", "c", authors=())]).record.authors == ()


def test_first_present_fields_follow_the_anchor_then_fill_gaps():
    a = rec(venue=None, url=None, oa_url=None, year=None)
    b = rec("crossref", "c", venue="J. Testing", url="https://pub.test/p", oa_url="https://oa.test/p.pdf", year=2021)
    c = rec("arxiv", "x", venue="Other J.", year=2019)
    result = merge_records([a, b, c])
    merged = result.record
    assert (merged.venue, merged.url, merged.oa_url, merged.year) == ("J. Testing", "https://pub.test/p", "https://oa.test/p.pdf", 2021)
    venue = next(d for d in result.decisions if d.field == "venue")
    assert venue.chosen_from == "crossref:c" and "arxiv:x" in venue.passed_over[0]


def test_anchor_values_that_are_present_are_not_overridden_and_agreement_is_not_logged():
    a, b = rec(venue="J. Testing", url="https://pub.test/p"), rec("crossref", "c", venue="J. Testing", url="https://pub.test/p")
    result = merge_records([a, b])
    assert result.record.venue == "J. Testing"
    assert [d.field for d in result.decisions] == []  # nothing disagreed, nothing to explain


def test_different_dois_are_refused():
    with pytest.raises(MergeError, match="different DOIs"):
        merge_records([rec(doi="10.1000/a"), rec("crossref", "c", doi="10.1000/b")])


def test_same_doi_in_different_spellings_is_fine_and_is_normalised():
    merged = merge_records([rec(doi="https://doi.org/10.1000/ABC"), rec("crossref", "c", doi="doi:10.1000/abc")]).record
    assert merged.doi == "10.1000/abc"


def test_empty_group_is_refused():
    with pytest.raises(MergeError):
        merge_records([])


def test_matched_by_says_how_each_member_joined():
    anchor = rec(doi="10.1000/abc")
    by_doi = rec("crossref", "c", doi="https://doi.org/10.1000/ABC", title="Different wording of the title here")
    by_title = rec("arxiv", "x", doi=None, authors=("Lovelace, A.",))
    unrelated_chain = rec("pubmed", "p", doi=None, title="Totally other title text", authors=("Someone Else",))
    matched = merge_records([anchor, by_doi, by_title, unrelated_chain]).matched_by
    assert matched == {
        "openalex:W1": "anchor", "crossref:c": "doi", "arxiv:x": "title_year_author", "pubmed:p": "group",
    }


def test_audit_payload_has_ids_and_descriptions_but_no_text():
    a = rec(abstract="Confidential abstract wording.", title="Secret title of the paper itself")
    b = rec("crossref", "c", abstract="Confidential abstract wording, longer.", title="Secret title of the paper itself")
    payload = merge_records([a, b]).audit_payload()
    assert payload["members"] == ["openalex:W1", "crossref:c"]
    text = str(payload)
    assert "Confidential" not in text and "Secret title" not in text
    assert any(d["field"] == "abstract" for d in payload["decisions"])


def test_end_to_end_with_grouping_nothing_is_lost():
    records = [
        rec("openalex", "W1", abstract=None, source_ids={"openalex": "W1"}),
        rec("semantic_scholar", "b" * 40, doi=None, abstract="Full abstract from S2.", source_ids={"semantic_scholar": "b" * 40}),
        rec("crossref", "10.1000/zzz", title="A completely unrelated paper about another subject", doi="10.1000/zzz", authors=("Sam Other",)),
    ]
    groups = group_duplicates(records)
    assert groups == [[0, 1], [2]]
    merged = merge_records([records[i] for i in groups[0]]).record
    assert merged.abstract == "Full abstract from S2." and set(merged.source_ids) == {"openalex", "semantic_scholar"}
    assert merged.doi == "10.1000/abc"


def test_merge_is_deterministic_and_does_not_mutate_inputs():
    group = [rec(abstract="One."), rec("crossref", "c", abstract="Two, longer.")]
    before = [g.model_dump() for g in group]
    assert merge_records(group) == merge_records(list(group))
    assert [g.model_dump() for g in group] == before
