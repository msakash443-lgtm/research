import pytest

from app.connectors import arxiv, core, crossref, ieee_xplore, openalex, pubmed, scopus, semantic_scholar, unpaywall, web_of_science
from app.connectors.base import clean_abstract, clean_text


def test_clean_text_collapses_whitespace_and_caps_length():
    assert clean_text("  Remote\n work\tand   output ", 200) == "Remote work and output"
    assert clean_text("abcdef", 3) == "abc"


def test_clean_text_returns_none_for_blank_or_non_text():
    for value in (None, "", "   \n", 42, ["title"]):
        assert clean_text(value, 10) is None


MESSY = "  Remote\n work  and\toutput  "
CLEAN = "Remote work and output"


def test_short_fields_collapse_whitespace_in_every_connector():
    records = [
        crossref.to_record({"DOI": "10.1234/a", "title": [MESSY], "container-title": [MESSY], "author": [{"family": MESSY}]}),
        openalex.to_record({"id": "W1", "display_name": MESSY, "authorships": [{"author": {"display_name": MESSY}}],
                            "primary_location": {"source": {"display_name": MESSY}}}),
        semantic_scholar.to_record({"paperId": "a" * 40, "title": MESSY, "venue": MESSY, "authors": [{"name": MESSY}]}),
        unpaywall.to_record({"doi": "10.1234/a", "title": MESSY, "journal_name": MESSY, "z_authors": [{"raw_author_name": MESSY}]}),
    ]
    for record in records:
        assert (record.title, record.venue, tuple(record.authors)) == (CLEAN, CLEAN, (CLEAN,)), record.connector


def test_clean_abstract_keeps_paragraphs_and_joins_wrapped_lines():
    raw = "  First line of the\r\n  first   paragraph.\n\n \n\tSecond paragraph,\nwrapped.\n"
    assert clean_abstract(raw, 20000) == "First line of the first paragraph.\n\nSecond paragraph, wrapped."


def test_clean_abstract_caps_length_and_rejects_blank_or_non_text():
    assert clean_abstract("One.\n\nTwo.", 6) == "One."
    for value in (None, "", " \n\n ", 42):
        assert clean_abstract(value, 10) is None


RAW_ABSTRACT = "  We study remote\n  work.\n\nWe find   more output.\n"
ABSTRACT = "We study remote work.\n\nWe find more output."
ARXIV_FEED = (
    '<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/2101.00001v1</id>'
    f"<title>A title long enough</title><summary>{RAW_ABSTRACT}</summary></entry></feed>"
)


@pytest.mark.parametrize("build", [
    lambda: semantic_scholar.to_record({"paperId": "a" * 40, "title": "A title long enough", "abstract": RAW_ABSTRACT}),
    lambda: core.to_record({"id": "123", "title": "A title long enough", "abstract": RAW_ABSTRACT}),
    lambda: ieee_xplore.to_record({"article_number": "123", "title": "A title long enough", "abstract": RAW_ABSTRACT}),
    lambda: scopus.to_record({"dc:identifier": "SCOPUS_ID:123", "dc:title": "A title long enough", "dc:description": RAW_ABSTRACT}),
    lambda: web_of_science.to_record({"uid": "WOS:000123456700001", "title": "A title long enough", "abstract": RAW_ABSTRACT}),
    lambda: web_of_science.to_record({"uid": "WOS:000123456700001", "title": "A title long enough", "abstract": {"value": RAW_ABSTRACT}}),
    lambda: arxiv.parse_feed(ARXIV_FEED)[0][0],
], ids=["semantic_scholar", "core", "ieee_xplore", "scopus", "web_of_science", "web_of_science_wrapped", "arxiv"])
def test_plain_text_abstracts_keep_paragraph_breaks_in_every_connector(build):
    record = build()
    assert record.abstract == ABSTRACT
    assert "no_abstract" not in record.quality_flags


def test_crossref_jats_abstract_keeps_paragraph_and_section_boundaries():
    record = crossref.to_record({
        "DOI": "10.1234/structured",
        "title": ["A structured abstract"],
        "abstract": "<jats:abstract xmlns:jats='urn:x'><jats:title>Abstract</jats:title><jats:sec><jats:title>Background</jats:title><jats:p>First line<br/>wrapped.</jats:p></jats:sec><jats:sec><jats:p>Second paragraph.</jats:p></jats:sec></jats:abstract>",
    })
    assert record.abstract == "Background\n\nFirst line wrapped.\n\nSecond paragraph."



def test_openalex_inverted_index_remains_one_abstract_paragraph():
    record = openalex.to_record({
        "id": "https://openalex.org/W123",
        "display_name": "A title",
        "abstract_inverted_index": {"First": [0], "paragraph": [1], "Second": [2], "part": [3]},
    })
    assert record.abstract == "First paragraph Second part"

def test_pubmed_labeled_abstract_sections_are_separate_paragraphs():
    import xml.etree.ElementTree as ET

    article = ET.fromstring(
        "<PubmedArticle><MedlineCitation><PMID>123</PMID><Article><ArticleTitle>Structured</ArticleTitle>"
        "<Abstract><AbstractText Label='BACKGROUND'>First line. Wrapped.</AbstractText>"
        "<AbstractText Label='RESULTS'>Second section.</AbstractText></Abstract></Article></MedlineCitation></PubmedArticle>"
    )
    record = pubmed.to_record(article)
    assert record.abstract == "BACKGROUND: First line. Wrapped.\n\nRESULTS: Second section."
    assert "no_abstract" not in record.quality_flags
