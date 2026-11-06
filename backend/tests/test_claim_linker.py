from app.claim_linker import ClaimFacts, SourceFacts, check_section


def _cite(source_id: str) -> dict:
    return {"type": "cite", "attrs": {"source_id": source_id}, "text": "(Lee 2020)"}


def _para(*children: dict) -> dict:
    return {"type": "paragraph", "content": list(children)}


def _doc(*paragraphs: dict) -> dict:
    return {"type": "doc", "content": list(paragraphs)}


def _text(value: str) -> dict:
    return {"type": "text", "text": value}


VERIFIED = SourceFacts(id="s1", verified=True, title="Remote teams")
UNVERIFIED = SourceFacts(id="s2", verified=False, title="Unchecked preprint")
SOURCES = {"s1": VERIFIED, "s2": UNVERIFIED}


def _kinds(report) -> list[str]:
    return sorted(f.kind for f in report.findings)


def test_a_section_whose_claims_and_cites_are_all_verified_has_no_findings():
    content = _doc(_para(_text("Teams share less."), _cite("s1")))
    claims = [ClaimFacts(claim_id="c1", verified=True, evidence_source_ids=("s1",))]
    report = check_section(content, claims, SOURCES)
    assert report.findings == []
    assert report.reference_source_ids == ["s1"]


def test_a_cite_to_an_unverified_source_is_flagged_and_left_out_of_the_reference_list():
    content = _doc(_para(_cite("s2")))
    report = check_section(content, [], SOURCES)
    assert _kinds(report) == ["unverified_citation"]
    assert report.findings[0].subject == "s2"
    assert report.reference_source_ids == []


def test_a_cite_to_a_source_not_in_the_project_is_flagged_as_unknown():
    report = check_section(_doc(_para(_cite("nope"))), [], SOURCES)
    assert _kinds(report) == ["unknown_source"]
    assert report.findings[0].subject == "nope"


def test_a_claim_with_no_evidence_is_an_orphan_and_is_listed_by_id():
    report = check_section(_doc(), [ClaimFacts(claim_id="c9", verified=False, evidence_source_ids=())], SOURCES)
    assert _kinds(report) == ["orphan_claim"]
    assert report.orphan_claim_ids == ["c9"]


def test_claim_evidence_from_an_unverified_source_is_flagged():
    content = _doc(_para(_cite("s2")))
    claims = [ClaimFacts(claim_id="c1", verified=False, evidence_source_ids=("s2",))]
    report = check_section(content, claims, SOURCES)
    assert "unverified_evidence" in _kinds(report)


def test_verified_claim_evidence_that_the_text_never_cites_is_flagged_so_the_reference_list_matches_the_text():
    content = _doc(_para(_text("No citation here.")))
    claims = [ClaimFacts(claim_id="c1", verified=True, evidence_source_ids=("s1",))]
    report = check_section(content, claims, SOURCES)
    assert _kinds(report) == ["evidence_not_cited"]
    assert report.findings[0].subject == "s1"


def test_citations_nested_inside_lists_are_found_and_the_reference_list_follows_first_use():
    content = _doc(
        {"type": "bullet_list", "content": [{"type": "list_item", "content": [_para(_cite("s1"))]}]},
        _para(_cite("s1"), _cite("s3")),
        _para(_cite("s3")),
    )
    sources = {**SOURCES, "s3": SourceFacts(id="s3", verified=True, title="Later")}
    report = check_section(content, [], sources)
    assert report.reference_source_ids == ["s1", "s3"]
    assert report.findings == []


def test_a_section_with_no_content_and_no_claims_has_no_findings():
    report = check_section({"type": "doc"}, [], SOURCES)
    assert report.findings == []
    assert report.ok
