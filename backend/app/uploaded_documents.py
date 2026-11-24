"""Accepted formats and text extraction for uploaded research documents (plan M8.1, M8.2).

Pure: no database, no storage. The router in `routers/uploaded_documents.py` does the I/O. Checks are
the media type, the leading signature bytes, and a full extraction, which is how a file with no text
layer, an encrypted PDF or a corrupt DOCX is refused at upload. There is no virus scanner and no OCR.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.docx_text import DocxTooLong, extract_docx_text
from app.fulltext import (
    FullText,
    FullTextError,
    NoExtractableText,
    PdfEncrypted,
    PdfTooManyPages,
    extract_pdf_text,
)

PDF_MEDIA_TYPE = "application/pdf"
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MAX_FILENAME_LENGTH = 255


class UnsupportedMediaType(ValueError):
    """The declared Content-Type is not one this endpoint accepts."""


class NotThatFormat(ValueError):
    """The bytes do not start with the signature of the declared format."""


@dataclass(frozen=True)
class Format:
    extension: str
    signature: bytes
    label: str


FORMATS: dict[str, Format] = {
    PDF_MEDIA_TYPE: Format("pdf", b"%PDF-", "PDF"),
    DOCX_MEDIA_TYPE: Format("docx", b"PK\x03\x04", "DOCX"),
}


def format_for(media_type: str) -> Format:
    try:
        return FORMATS[media_type]
    except KeyError as exc:
        raise UnsupportedMediaType(media_type) from exc


def check_signature(fmt: Format, data: bytes) -> None:
    if not data.startswith(fmt.signature):
        raise NotThatFormat(fmt.label)


def extract_text(fmt: Format, data: bytes, *, max_pdf_pages: int, max_docx_paragraphs: int) -> FullText:
    """Extract text with page/section anchors. Raises a `FullTextError` subclass when it can't."""
    if fmt.extension == "pdf":
        return extract_pdf_text(data, max_pages=max_pdf_pages)
    return extract_docx_text(data, max_paragraphs=max_docx_paragraphs)


def error_code(exc: FullTextError) -> str:
    """The stable code a 422 response carries for an extraction failure."""
    if isinstance(exc, NoExtractableText):
        return "no_extractable_text"
    if isinstance(exc, PdfEncrypted):
        return "encrypted"
    if isinstance(exc, (PdfTooManyPages, DocxTooLong)):
        return "too_long"
    return "unreadable"
