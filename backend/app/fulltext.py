"""PDF text extraction with page and section anchors (plan M2.7.3).

`extract_pdf_text(data)` turns PDF bytes into a `FullText`: the document text with every page's
character range, the section headings found in it, and the pages that had no extractable text.
`FullText.find_quote(quote, page=..., section=...)` locates a quote in that text and says which page(s)
and section it sits in; verbatim span verification (M3.4) is built on it.

Quote matching is verbatim apart from layout noise a PDF adds and a reader can't see:
  - Unicode compatibility forms (NFKD: ligatures like "ﬁ", full-width letters, precomposed vs
    combining accents) and invisible
    characters (soft hyphens, zero-width spaces);
  - whitespace: any run of spaces/newlines equals one space;
  - curly quotes and apostrophes equal straight ones; en/em dashes and minus signs equal "-";
  - a word split across a line break with a hyphen ("pro-\\nposed") matches both "proposed" and
    "pro-posed", so "self-\\nreport" still matches "self-report".
Case, punctuation and word order are never normalised: a quote that differs in any of those is not
in the text.

Failures are loud (plan §1.6): an unreadable, encrypted or page-capped PDF raises, and a PDF with no
extractable text at all (a scan) raises rather than returning empty text. OCR is out of scope.

The extracted text is untrusted data: anything that puts it into a prompt goes through
`untrusted_text.clean_untrusted` first. This module never changes the text it extracts.
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
import unicodedata
from bisect import bisect_right
from dataclasses import dataclass
from functools import cached_property

from pypdf import PdfReader
from pypdf.errors import DependencyError, FileNotDecryptedError, PdfReadError

logger = logging.getLogger(__name__)

# Text between pages: a blank line, so a quote can't silently run from one page's last word into the next.
PAGE_SEPARATOR = "\n\n"


class FullTextError(Exception):
    """Base class for PDF text extraction failures."""


class PdfUnreadable(FullTextError):
    """The bytes are not a PDF pypdf can parse."""


class PdfEncrypted(FullTextError):
    """The PDF is password-protected and can't be opened without the password."""


class PdfTooManyPages(FullTextError):
    """The PDF has more pages than the configured cap."""


class NoExtractableText(FullTextError):
    """No page has any text layer — most likely a scan. OCR is not supported."""


@dataclass(frozen=True)
class Page:
    number: int  # physical page, 1-based
    label: str | None  # printed page label from the PDF (e.g. "iv", "123"), if it defines one
    start: int  # offsets into FullText.text, end exclusive
    end: int


@dataclass(frozen=True)
class Section:
    title: str  # heading as printed, numbering included ("2.1 Methods")
    name: str  # canonical name ("methods"), used for matching
    page: int  # physical page the heading is on
    start: int  # offset of the heading in FullText.text


@dataclass(frozen=True)
class QuoteMatch:
    start: int  # offsets into FullText.text, end exclusive
    end: int
    pages: tuple[int, ...]  # physical pages the match touches
    section: Section | None  # section the match starts in, None before the first heading


@dataclass(frozen=True)
class FullText:
    text: str
    pages: tuple[Page, ...]
    sections: tuple[Section, ...]
    empty_pages: tuple[int, ...]  # physical pages with no text layer (figures, scanned inserts)
    sha256: str  # of the PDF bytes, so a quote can be tied to the exact file

    def page_at(self, offset: int) -> Page:
        """The page containing `offset` (a separator belongs to the page before it)."""
        if not 0 <= offset <= len(self.text):
            raise IndexError(f"offset {offset} is outside the text (length {len(self.text)})")
        index = bisect_right([p.start for p in self.pages], offset) - 1
        return self.pages[max(index, 0)]

    def section_at(self, offset: int) -> Section | None:
        index = bisect_right([s.start for s in self.sections], offset) - 1
        return self.sections[index] if index >= 0 else None

    def find_quote(self, quote: str, *, page: int | str | None = None, section: str | None = None) -> list[QuoteMatch]:
        """Every place `quote` occurs (see the module docstring for what counts as the same text).

        `page` keeps matches that touch that page, given either as the physical page number or as the
        printed label. `section` keeps matches inside the section with that name or title (case and
        numbering ignored). An empty quote matches nothing.
        """
        pattern = _quote_pattern(quote)
        if pattern is None:
            return []
        norm, index_map = self._normalised
        matches = []
        for found in pattern.finditer(norm):
            start = index_map[found.start()]
            end = index_map[found.end() - 1] + 1
            pages = tuple(p.number for p in self.pages if p.start < end and start < p.end)
            match = QuoteMatch(start=start, end=end, pages=pages, section=self.section_at(start))
            if page is not None and not self._touches_page(match, page):
                continue
            if section is not None and not _section_matches(match.section, section):
                continue
            matches.append(match)
        return matches

    @cached_property
    def _normalised(self) -> tuple[str, list[int]]:
        return _normalise_with_map(self.text)

    def _touches_page(self, match: QuoteMatch, page: int | str) -> bool:
        wanted = str(page).strip()
        return any(str(p.number) == wanted or (p.label is not None and p.label == wanted) for p in self.pages if p.number in match.pages)


def extract_pdf_text(data: bytes, *, max_pages: int) -> FullText:
    """Extract the text of a PDF with page and section anchors. Raises a `FullTextError` subclass on failure."""
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            _decrypt_without_password(reader)
        page_count = len(reader.pages)
    except FullTextError:
        raise
    except Exception as exc:  # pypdf raises many types on malformed input; all mean "can't read it"
        raise PdfUnreadable(f"not a readable PDF: {exc}") from exc
    if page_count > max_pages:
        raise PdfTooManyPages(f"PDF has {page_count} pages; the limit is {max_pages} (FULLTEXT_MAX_PAGES)")

    labels = _page_labels(reader, page_count)
    parts: list[str] = []
    pages: list[Page] = []
    empty: list[int] = []
    offset = 0
    for index, pdf_page in enumerate(reader.pages):
        try:
            page_text = pdf_page.extract_text() or ""
        except Exception as exc:
            raise PdfUnreadable(f"page {index + 1} could not be read: {exc}") from exc
        if not page_text.strip():
            empty.append(index + 1)
        if index:
            parts.append(PAGE_SEPARATOR)
            offset += len(PAGE_SEPARATOR)
        pages.append(Page(number=index + 1, label=labels[index], start=offset, end=offset + len(page_text)))
        parts.append(page_text)
        offset += len(page_text)

    if len(empty) == page_count:
        raise NoExtractableText(f"none of the {page_count} pages has a text layer (scanned PDF?); OCR is not supported")

    text = "".join(parts)
    result_pages = tuple(pages)
    return FullText(
        text=text,
        pages=result_pages,
        sections=_find_sections(text, result_pages),
        empty_pages=tuple(empty),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def _decrypt_without_password(reader: PdfReader) -> None:
    """Open a PDF that is encrypted with an empty user password (common for 'no copy' flags); refuse otherwise."""
    try:
        opened = reader.decrypt("")
    except (DependencyError, FileNotDecryptedError, PdfReadError, NotImplementedError) as exc:
        raise PdfEncrypted(f"PDF is encrypted and can't be opened: {exc}") from exc
    if not opened:
        raise PdfEncrypted("PDF is password-protected; upload an unprotected copy")


def _page_labels(reader: PdfReader, page_count: int) -> list[str | None]:
    """Printed page labels when the PDF defines them; pypdf falls back to "1", "2", ... otherwise."""
    try:
        labels = list(reader.page_labels)
    except Exception:  # a broken label tree shouldn't stop extraction; physical numbers still work
        logger.warning("PDF page labels unreadable; using physical page numbers only", exc_info=True)
        return [None] * page_count
    if len(labels) != page_count:
        return [None] * page_count
    if labels == [str(i + 1) for i in range(page_count)]:
        return [None] * page_count  # no real labels: don't pretend there are
    return labels


# --- section headings --------------------------------------------------------------------------
_SECTION_NAMES = {
    "abstract": "abstract",
    "summary": "abstract",
    "introduction": "introduction",
    "background": "background",
    "literature review": "literature review",
    "related work": "literature review",
    "theoretical framework": "theory",
    "theoretical background": "theory",
    "theory": "theory",
    "hypotheses": "hypotheses",
    "hypothesis development": "hypotheses",
    "method": "methods",
    "methods": "methods",
    "methodology": "methods",
    "materials and methods": "methods",
    "research design": "methods",
    "data": "data",
    "data and methods": "methods",
    "results": "results",
    "findings": "results",
    "analysis": "results",
    "discussion": "discussion",
    "general discussion": "discussion",
    "results and discussion": "discussion",
    "limitations": "limitations",
    "limitations and future research": "limitations",
    "future research": "future research",
    "implications": "implications",
    "practical implications": "implications",
    "theoretical implications": "implications",
    "conclusion": "conclusion",
    "conclusions": "conclusion",
    "concluding remarks": "conclusion",
    "acknowledgements": "acknowledgements",
    "acknowledgments": "acknowledgements",
    "references": "references",
    "bibliography": "references",
    "appendix": "appendix",
}
# Optional numbering ("2", "2.1.", "II.", "A."), then the heading words, then an optional colon.
_NUMBERING = r"(?:(?:\d{1,2}(?:\.\d{1,2}){0,3}|[IVXLC]{1,6}|[A-H])\.?\s+)?"
_HEADING = re.compile(rf"^\s*({_NUMBERING})([A-Za-z][A-Za-z &]{{1,60}}?)\s*:?\s*$")


def _canonical_heading(words: str) -> str | None:
    key = " ".join(words.replace("&", "and").casefold().split())
    if key in _SECTION_NAMES:
        return _SECTION_NAMES[key]
    if key.startswith("appendix"):
        return "appendix"
    return None


def _find_sections(text: str, pages: tuple[Page, ...]) -> tuple[Section, ...]:
    """Headings are whole lines naming a standard section; anything else is body text."""
    sections = []
    for page in pages:
        line_start = page.start
        for line in text[page.start : page.end].split("\n"):
            found = _HEADING.match(line)
            if found:
                name = _canonical_heading(found.group(2))
                if name is not None:
                    leading = len(line) - len(line.lstrip())
                    sections.append(Section(title=line.strip().rstrip(":").strip(), name=name, page=page.number, start=line_start + leading))
            line_start += len(line) + 1
    return tuple(sections)


def _heading_words(heading: str) -> str:
    """The heading without numbering, casefolded, whitespace collapsed ("2.1 Methods:" -> "methods")."""
    found = _HEADING.match(heading)
    return " ".join((found.group(2) if found else heading).casefold().split())


def _section_matches(found: Section | None, wanted: str) -> bool:
    """`wanted` names the section by its title or by a heading with the same canonical name."""
    if found is None:
        return False
    words = _heading_words(wanted)
    return words == _heading_words(found.title) or words == found.name or _canonical_heading(words) == found.name


# --- quote normalisation -----------------------------------------------------------------------
# The text side keeps a marker where a hyphen ended a line, so the quote can match with or without it.
_LINE_HYPHEN = "\x00"
_PUNCT = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-",
})


def _normalise_with_map(text: str) -> tuple[str, list[int]]:
    """Normalised text plus, for each of its characters, the offset of the original character it came from."""
    out: list[str] = []
    origin: list[int] = []
    pending_space = False
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            pending_space = bool(out)
            i += 1
            continue
        if ch in "-‐­" and out and out[-1].isalnum():
            # A hyphen followed only by whitespace containing a line break, then a letter: a line-end split.
            j = i + 1
            while j < n and text[j].isspace() and text[j] not in "\n\r":
                j += 1
            if j < n and text[j] in "\n\r":
                while j < n and text[j].isspace():
                    j += 1
                if j < n and text[j].isalnum():
                    out.append(_LINE_HYPHEN)
                    origin.append(i)
                    pending_space = False
                    i = j
                    continue
        if ch == "­" or unicodedata.category(ch) == "Cf":
            i += 1  # soft hyphen or zero-width character: invisible
            continue
        if pending_space:
            out.append(" ")
            origin.append(i - 1)
            pending_space = False
        for piece in unicodedata.normalize("NFKD", ch).translate(_PUNCT):
            if piece.isspace():
                continue
            out.append(piece)
            origin.append(i)
        i += 1
    return "".join(out), origin


def _quote_pattern(quote: str) -> re.Pattern[str] | None:
    norm, _ = _normalise_with_map(quote)
    norm = norm.replace(_LINE_HYPHEN, "-")  # a line-split word inside the quote itself means the hyphen
    if not norm:
        return None
    parts = []
    for index, ch in enumerate(norm):
        if ch == "-":
            parts.append(f"[-{_LINE_HYPHEN}]")
        else:
            # After any character but the last, the text may hold a dropped line-end hyphen.
            parts.append(re.escape(ch) + (f"{_LINE_HYPHEN}?" if index < len(norm) - 1 else ""))
    return re.compile("".join(parts))
