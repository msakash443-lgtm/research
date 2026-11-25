"""DOCX text extraction with heading-based section anchors (plan M8.2).

`extract_docx_text(data)` returns a `fulltext.FullText`, so the uploaded-document content route and any
later quote check handle DOCX and PDF the same way. Pure: no database, no storage.

What is extracted: the body paragraphs, in order, one per line. Table cells, headers, footers, footnotes
and text boxes are not extracted, so quotes from them will not verify. A DOCX has no pages, so
`pages` and `empty_pages` are empty and every `Section.page` is 0.

Sections come from paragraphs styled Heading 1-3. A heading that names a standard section (see
`fulltext._canonical_heading`) gets that canonical name, the same as in PDFs; any other heading is
still a section, named by its casefolded title, so a quote can be located by its heading.

Failures raise a `FullTextError` subclass, the same classes PDF extraction uses where they apply:
`DocxUnreadable` (not a readable DOCX, or an archive over the limits below), `DocxTooLong` (more
paragraphs than the cap) and `fulltext.NoExtractableText` (no text at all).

A DOCX is a zip, so a few kilobytes can declare gigabytes. Before python-docx parses anything the
archive directory is checked: at most `MAX_DOCX_PARTS` entries and `MAX_DOCX_UNCOMPRESSED_BYTES` of
declared uncompressed size. The zip reader never inflates past an entry's declared size, so the
declared sizes are an effective bound.
"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile

from docx import Document

from app.fulltext import FullText, FullTextError, NoExtractableText, Section, _canonical_heading

_HEADING_STYLE = re.compile(r"^heading ([1-3])$", re.IGNORECASE)

MAX_DOCX_PARTS = 1000
MAX_DOCX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024


class DocxError(FullTextError):
    """Base class for DOCX extraction failures."""


class DocxUnreadable(DocxError):
    """The bytes are not a DOCX python-docx can open."""


class DocxTooLong(DocxError):
    """The DOCX has more body paragraphs than the configured cap."""


def _check_archive(data: bytes) -> None:
    try:
        parts = zipfile.ZipFile(io.BytesIO(data)).infolist()
    except zipfile.BadZipFile as exc:
        raise DocxUnreadable(f"not a readable DOCX: {exc}") from exc
    if len(parts) > MAX_DOCX_PARTS:
        raise DocxUnreadable(f"DOCX has {len(parts)} parts; the limit is {MAX_DOCX_PARTS}")
    total = sum(part.file_size for part in parts)
    if total > MAX_DOCX_UNCOMPRESSED_BYTES:
        raise DocxUnreadable(f"DOCX expands to {total} bytes; the limit is {MAX_DOCX_UNCOMPRESSED_BYTES}")


def extract_docx_text(data: bytes, *, max_paragraphs: int) -> FullText:
    _check_archive(data)
    try:
        document = Document(io.BytesIO(data))
        paragraphs = document.paragraphs
    except Exception as exc:  # python-docx raises several types on malformed input; all mean "can't read it"
        raise DocxUnreadable(f"not a readable DOCX: {exc}") from exc
    if len(paragraphs) > max_paragraphs:
        raise DocxTooLong(f"DOCX has {len(paragraphs)} paragraphs; the limit is {max_paragraphs}")

    lines = [paragraph.text for paragraph in paragraphs]
    if not any(line.strip() for line in lines):
        raise NoExtractableText("the DOCX has no text in its body paragraphs")

    parts: list[str] = []
    sections: list[Section] = []
    offset = 0
    for index, (paragraph, line) in enumerate(zip(paragraphs, lines)):
        if index:
            parts.append("\n")
            offset += 1
        style = paragraph.style.name if paragraph.style is not None else ""
        if _HEADING_STYLE.match(style) and line.strip():
            title = " ".join(line.split())
            name = _canonical_heading(title) or title.casefold()
            sections.append(Section(title=title, name=name, page=0, start=offset))
        parts.append(line)
        offset += len(line)

    text = "".join(parts)
    return FullText(
        text=text,
        pages=(),
        sections=tuple(sections),
        empty_pages=(),
        sha256=hashlib.sha256(data).hexdigest(),
    )
