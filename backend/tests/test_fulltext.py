"""PDF text extraction with page/section anchors (plan M2.7.3). PDFs are built in-test; no files, no network."""

import hashlib
import io

import pytest
from pypdf import PdfReader, PdfWriter

from app.config import Settings
from app.fulltext import (
    FullTextError,
    NoExtractableText,
    PdfEncrypted,
    PdfTooManyPages,
    PdfUnreadable,
    extract_pdf_text,
)


def _escape(line: str) -> str:
    return line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages: list[list[str]]) -> bytes:
    """A minimal PDF: one Helvetica line per string, top to bottom. An empty list is a page with no text."""
    objects: list[bytes] = []
    page_ids = []
    font_id = 3
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(b"")  # pages tree, filled in below
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    for lines in pages:
        ops = ["BT", "/F1 11 Tf", "14 TL", "72 760 Td"]
        for line in lines:
            ops.append(f"({_escape(line)}) Tj T*")
        ops.append("ET")
        stream = "\n".join(ops).encode("cp1252")
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        content_id = len(objects)
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents %d 0 R /Resources << /Font << /F1 %d 0 R >> >> >>"
            % (content_id, font_id)
        )
        page_ids.append(len(objects))
    kids = " ".join(f"{i} 0 R" for i in page_ids).encode()
    objects[1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, len(page_ids))

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % number + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref))
    return out.getvalue()


PAPER = [
    [
        "Abstract",
        "We study burnout among nurses in public hospitals.",
        "1. Introduction",
        "Burnout is a growing concern for health systems.",
    ],
    [
        "2. Methods",
        "We surveyed 412 nurses using a self-",
        "report questionnaire that was pro-",
        "posed by earlier work.",
    ],
    [
        "3. Results",
        "Emotional exhaustion predicted turnover intention (beta = 0.41).",
        "References",
        "Maslach, C. (1981). Burnout.",
    ],
]


def extract(pages=PAPER, max_pages=100):
    return extract_pdf_text(make_pdf(pages), max_pages=max_pages)


# --- pages ---------------------------------------------------------------------------------------
def test_each_page_range_holds_that_pages_text():
    doc = extract()
    assert [p.number for p in doc.pages] == [1, 2, 3]
    assert "burnout among nurses" in doc.text[doc.pages[0].start : doc.pages[0].end]
    assert "412 nurses" in doc.text[doc.pages[1].start : doc.pages[1].end]
    assert "turnover intention" in doc.text[doc.pages[2].start : doc.pages[2].end]
    assert doc.page_at(doc.text.index("412")).number == 2


def test_sha256_is_of_the_pdf_bytes():
    data = make_pdf(PAPER)
    assert extract_pdf_text(data, max_pages=10).sha256 == hashlib.sha256(data).hexdigest()


def test_page_labels_are_kept_when_the_pdf_defines_them():
    writer = PdfWriter(clone_from=io.BytesIO(make_pdf(PAPER)))
    writer.set_page_label(0, 2, style="/D", start=101)
    buffer = io.BytesIO()
    writer.write(buffer)
    doc = extract_pdf_text(buffer.getvalue(), max_pages=10)
    assert [p.label for p in doc.pages] == ["101", "102", "103"]
    # a quote can be located by printed label or by physical page
    assert doc.find_quote("412 nurses", page="102")
    assert doc.find_quote("412 nurses", page=2)
    assert not doc.find_quote("412 nurses", page="101")


def test_without_labels_label_is_none():
    assert all(p.label is None for p in extract().pages)


def test_empty_pages_are_reported_but_do_not_fail():
    doc = extract([PAPER[0], [], PAPER[2]])
    assert doc.empty_pages == (2,)
    assert doc.pages[1].start == doc.pages[1].end


# --- sections ------------------------------------------------------------------------------------
def test_standard_headings_become_sections_with_pages():
    doc = extract()
    assert [(s.name, s.page) for s in doc.sections] == [
        ("abstract", 1),
        ("introduction", 1),
        ("methods", 2),
        ("results", 3),
        ("references", 3),
    ]
    methods = doc.sections[2]
    assert methods.title == "2. Methods"
    assert doc.text[methods.start :].startswith("2. Methods")


def test_body_lines_mentioning_a_section_word_are_not_headings():
    doc = extract([["The results of the discussion were mixed.", "See the methods we used."]])
    assert doc.sections == ()


# --- quotes --------------------------------------------------------------------------------------
def test_quote_found_with_page_and_section():
    doc = extract()
    [match] = doc.find_quote("Emotional exhaustion predicted turnover intention")
    assert match.pages == (3,)
    assert match.section.name == "results"
    assert doc.text[match.start : match.end] == "Emotional exhaustion predicted turnover intention"


def test_quote_across_lines_matches_with_any_whitespace():
    doc = extract()
    assert doc.find_quote("Abstract\n We   study burnout")


def test_line_end_hyphen_matches_joined_and_hyphenated_forms():
    doc = extract()
    assert doc.find_quote("questionnaire that was proposed by earlier work")  # pro-/posed
    assert doc.find_quote("using a self-report questionnaire")  # self-/report keeps its hyphen
    assert doc.find_quote("using a selfreport questionnaire")  # the text can't tell the two apart


def test_match_offsets_cover_the_original_text_including_line_break():
    doc = extract()
    [match] = doc.find_quote("was proposed")
    original = doc.text[match.start : match.end]
    assert original.startswith("was pro-") and original.endswith("posed")


def test_quote_ending_at_a_line_end_hyphen_does_not_include_it():
    doc = extract()
    [match] = doc.find_quote("using a self")
    assert doc.text[match.start : match.end] == "using a self"


def test_quote_crossing_a_page_break_reports_both_pages():
    doc = extract()
    [match] = doc.find_quote("growing concern for health systems. 2. Methods")
    assert match.pages == (1, 2)


def test_case_wording_and_punctuation_are_not_normalised():
    doc = extract()
    assert not doc.find_quote("emotional exhaustion predicted turnover intention")
    assert not doc.find_quote("Emotional exhaustion predicts turnover intention")
    assert not doc.find_quote("(beta = 0.42)")
    assert not doc.find_quote("We surveyed 421 nurses")


def test_typographic_variants_match():
    doc = extract([["The \"so-called\" effect isn't new."]])
    assert doc.find_quote("The “so–called” effect isn’t new.")
    assert doc.find_quote("The \"so-called\" effect isn­'t new.")  # soft hyphen is invisible


def test_ligature_in_the_quote_matches_plain_letters():
    doc = extract([["the effect was significant"]])
    assert doc.find_quote("the eﬀect was signiﬁcant")


def test_decomposed_accents_match_precomposed():
    doc = extract([["Café workers in München"]])
    assert doc.find_quote("Café workers in München")


def test_page_and_section_filters():
    doc = extract()
    assert doc.find_quote("412 nurses", section="Methods")
    assert doc.find_quote("412 nurses", section="2. Methods")
    assert doc.find_quote("412 nurses", section="methodology")  # same canonical section
    assert not doc.find_quote("412 nurses", section="Results")
    assert not doc.find_quote("412 nurses", page=3)


def test_quote_before_any_heading_has_no_section():
    doc = extract([["Preface text here.", "Introduction", "Body."]])
    [match] = doc.find_quote("Preface text here.")
    assert match.section is None
    assert not doc.find_quote("Preface text here.", section="Introduction")


def test_repeated_quote_returns_every_occurrence():
    doc = extract([["burnout matters"], ["burnout matters"]])
    assert [m.pages for m in doc.find_quote("burnout matters")] == [(1,), (2,)]


def test_empty_or_whitespace_quote_matches_nothing():
    doc = extract()
    assert doc.find_quote("") == []
    assert doc.find_quote("   \n") == []


def test_quote_with_regex_characters_is_literal():
    doc = extract()
    assert doc.find_quote("(beta = 0.41).")
    assert not doc.find_quote("(beta = 0.4.)")


# --- failures are loud ---------------------------------------------------------------------------
def test_not_a_pdf_raises():
    with pytest.raises(PdfUnreadable):
        extract_pdf_text(b"<html>not a pdf</html>", max_pages=10)


def test_truncated_pdf_raises_a_fulltext_error():
    data = make_pdf(PAPER)
    with pytest.raises(FullTextError):
        extract_pdf_text(data[: len(data) // 3], max_pages=10)


def test_scanned_pdf_with_no_text_layer_raises():
    with pytest.raises(NoExtractableText):
        extract([[], []])


def test_page_cap_is_enforced_before_extraction():
    with pytest.raises(PdfTooManyPages, match="3 pages; the limit is 2"):
        extract(max_pages=2)


def test_password_protected_pdf_raises():
    writer = PdfWriter(clone_from=io.BytesIO(make_pdf(PAPER)))
    writer.encrypt(user_password="secret", owner_password="owner", algorithm="RC4-128")
    buffer = io.BytesIO()
    writer.write(buffer)
    with pytest.raises(PdfEncrypted):
        extract_pdf_text(buffer.getvalue(), max_pages=10)


def test_pdf_encrypted_with_empty_user_password_is_read():
    writer = PdfWriter(clone_from=io.BytesIO(make_pdf(PAPER)))
    writer.encrypt(user_password="", owner_password="owner", algorithm="RC4-128")
    buffer = io.BytesIO()
    writer.write(buffer)
    assert PdfReader(io.BytesIO(buffer.getvalue())).is_encrypted
    assert extract_pdf_text(buffer.getvalue(), max_pages=10).find_quote("412 nurses")


def test_page_cap_setting_exists_with_a_positive_default():
    assert Settings().fulltext_max_pages > 0
