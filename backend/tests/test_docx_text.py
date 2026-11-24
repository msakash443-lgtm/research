"""Pure DOCX extraction (plan M8.2): headings become sections, body text keeps its order, and each failure
raises the exception class the upload route maps to a 422 code."""

import hashlib
import io

import pytest
from docx import Document as DocxDocument

from app.docx_text import DocxTooLong, DocxUnreadable, extract_docx_text
from app.fulltext import NoExtractableText


def _docx(build) -> bytes:
    buffer = io.BytesIO()
    doc = DocxDocument()
    build(doc)
    doc.save(buffer)
    return buffer.getvalue()


def test_a_heading_that_is_not_a_standard_section_is_a_section_named_by_its_casefolded_title():
    data = _docx(lambda d: (d.add_heading("Study Sample", level=2), d.add_paragraph("Adults aged 18 to 65.")))

    text = extract_docx_text(data, max_paragraphs=100)

    assert [(s.title, s.name) for s in text.sections] == [("Study Sample", "study sample")]
    assert text.text[text.sections[0].start:].startswith("Study Sample")
    assert text.pages == ()
    assert text.empty_pages == ()
    assert text.sha256 == hashlib.sha256(data).hexdigest()


def test_a_quote_under_a_method_heading_is_located_in_the_methods_section():
    data = _docx(lambda d: (d.add_heading("Method", level=1), d.add_paragraph("We surveyed 40 clinics.")))

    matches = extract_docx_text(data, max_paragraphs=100).find_quote("We surveyed 40 clinics.", section="methods")

    assert len(matches) == 1
    assert matches[0].section is not None and matches[0].section.name == "methods"


def test_a_docx_with_more_body_paragraphs_than_the_cap_is_refused_as_too_long():
    data = _docx(lambda d: [d.add_paragraph(f"line {i}") for i in range(3)])

    with pytest.raises(DocxTooLong):
        extract_docx_text(data, max_paragraphs=2)


def test_bytes_that_only_start_like_a_docx_are_refused_as_unreadable():
    with pytest.raises(DocxUnreadable):
        extract_docx_text(b"PK\x03\x04 not a zip archive", max_paragraphs=100)


def test_a_docx_whose_body_paragraphs_are_all_blank_has_no_extractable_text():
    data = _docx(lambda d: (d.add_paragraph(""), d.add_paragraph("   ")))

    with pytest.raises(NoExtractableText):
        extract_docx_text(data, max_paragraphs=100)
