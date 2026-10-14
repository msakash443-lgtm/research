"""Similarity check against project sources (X.31.14), pure logic.

Each test is one rule from the design (B6): verbatim and close-paraphrase flags, cited quotations skipped,
uncited quotations flagged, own text never flagged, short sentences skipped, offsets into the document's
plain text, and no source text in the findings.
"""

from app.similarity import Paragraph, Reference, check, summarise

REF_ID = "11111111-1111-1111-1111-111111111111"
EXCERPT_ID = "22222222-2222-2222-2222-222222222222"
SOURCE_SENTENCE = "Unpaid care work limits how many hours women can spend in paid employment each week."
REFERENCES = [Reference(source_id=REF_ID, excerpt_id=EXCERPT_ID, text=SOURCE_SENTENCE)]


def test_verbatim_copy_is_near_verbatim_with_the_source_and_offsets():
    findings = check([Paragraph(SOURCE_SENTENCE)], REFERENCES)

    assert findings == [{
        "start": 0,
        "end": len(SOURCE_SENTENCE),
        "kind": "near_verbatim",
        "source_id": REF_ID,
        "excerpt_id": EXCERPT_ID,
        "score": 1.0,
    }]


def test_case_and_punctuation_do_not_hide_a_copy():
    copy = "UNPAID care-work limits how many hours women can spend in paid employment each week!"

    findings = check([Paragraph(copy)], REFERENCES)

    assert [f["kind"] for f in findings] == ["near_verbatim"]


def test_reworded_copy_is_a_close_paraphrase():
    # Ratio 0.75 on the token sequences, 10 of 13 content words shared: inside the paraphrase band.
    reworded = "Unpaid care work restricts how many hours women are able to spend in paid jobs each week."

    findings = check([Paragraph(reworded)], REFERENCES)

    assert [f["kind"] for f in findings] == ["close_paraphrase"]
    assert findings[0]["source_id"] == REF_ID
    assert 0.6 <= findings[0]["score"] < 0.85


def test_own_unrelated_text_is_not_flagged():
    own = "Interviews with twenty nurses revealed shifting attitudes toward night shifts."

    assert check([Paragraph(own)], REFERENCES) == []


def test_cited_quotation_is_not_flagged():
    findings = check([Paragraph(SOURCE_SENTENCE, quoted=True, cited=True)], REFERENCES)

    assert findings == []


def test_uncited_quotation_is_flagged_with_the_source_it_matches():
    findings = check([Paragraph(SOURCE_SENTENCE, quoted=True, cited=False)], REFERENCES)

    assert len(findings) == 1
    assert findings[0]["kind"] == "quote_without_citation"
    assert (findings[0]["start"], findings[0]["end"]) == (0, len(SOURCE_SENTENCE))
    assert findings[0]["source_id"] == REF_ID


def test_uncited_quotation_with_no_matching_source_is_still_flagged():
    quote = "This passage was copied from a report we never filed in the project sources at all."

    findings = check([Paragraph(quote, quoted=True, cited=False)], REFERENCES)

    assert [f["kind"] for f in findings] == ["quote_without_citation"]
    assert findings[0]["source_id"] is None


def test_sentences_under_eight_words_are_not_checked():
    short_copy = "Care is unpaid."
    references = [Reference(source_id=REF_ID, excerpt_id=None, text=short_copy)]

    assert check([Paragraph(short_copy)], references) == []


def test_offsets_point_at_the_sentence_inside_the_paragraph():
    # The first sentence is under eight words, so only the copied second sentence is flagged. Offsets are into
    # the plain text, one line per paragraph, so the second paragraph starts after the first plus a newline.
    intro = "Our sample is small."
    paragraphs = [Paragraph(f"{intro} {SOURCE_SENTENCE}"), Paragraph(SOURCE_SENTENCE)]

    findings = check(paragraphs, REFERENCES)

    second_start = len(intro) + 1 + len(SOURCE_SENTENCE) + 1
    assert [(f["start"], f["end"]) for f in findings] == [
        (len(intro) + 1, len(intro) + 1 + len(SOURCE_SENTENCE)),
        (second_start, second_start + len(SOURCE_SENTENCE)),
    ]


def test_findings_never_carry_source_or_document_text():
    findings = check([Paragraph(SOURCE_SENTENCE)], REFERENCES)

    assert all(set(f) == {"start", "end", "kind", "source_id", "excerpt_id", "score"} for f in findings)


def test_summary_counts_each_kind():
    findings = check(
        [Paragraph(SOURCE_SENTENCE), Paragraph("Unpaid care work restricts how many hours women are able to spend in paid jobs each week.")],
        REFERENCES,
    )

    assert summarise(findings) == {"near_verbatim": 1, "close_paraphrase": 1, "quote_without_citation": 0}
