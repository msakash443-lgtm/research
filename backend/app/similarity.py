"""Similarity check of a manuscript section against this project's sources (X.31.14).

Pure: no database, no network, no model. Each sentence of at least MIN_WORDS words is compared with the reference
sentences (excerpts and abstracts of the project's sources) that share content words with it. A near copy is
`near_verbatim`; a reworded copy is `close_paraphrase`; a quotation with no citation is `quote_without_citation`.
Findings hold offsets and ids only, never the text of either side.

Offsets are into the document's plain text: one line per paragraph, as `ledger.plain_text` writes it.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher

from app.note_suggest import tokens as content_tokens

MIN_WORDS = 8  # sentences shorter than this are not checked
LONG_RUN = 8  # a shared run of this many words is a verbatim copy
NEAR_RATIO = 0.85
PARAPHRASE_RATIO = 0.60
PARAPHRASE_OVERLAP = 0.60  # share of the sentence's content words that the source also has
MIN_SHARED_CONTENT = 2  # a source sentence must share this many content words to be compared at all
MAX_CANDIDATES = 10

KINDS = ("near_verbatim", "close_paraphrase", "quote_without_citation")

_SENTENCE = re.compile(r"[^.!?]+[.!?]*")


@dataclass(frozen=True)
class Paragraph:
    text: str
    quoted: bool = False  # the whole paragraph is a quotation (paste=quotation)
    cited: bool = False  # the paragraph carries a citation node


@dataclass(frozen=True)
class Reference:
    source_id: str
    excerpt_id: str | None
    text: str


@dataclass(frozen=True)
class _Entry:
    reference: Reference
    tokens: list[str]
    content: frozenset[str]


@dataclass(frozen=True)
class _Match:
    ratio: float
    run: int
    overlap: float
    entry: _Entry


def normalise(text: str) -> list[str]:
    """Lowercase words, NFKC-folded, punctuation removed, whitespace collapsed."""
    text = unicodedata.normalize("NFKC", text).lower()
    return re.sub(r"[^\w\s]", " ", text).split()


def _sentences(text: str, offset: int) -> Iterator[tuple[int, int, str]]:
    """(start, end, sentence) with whitespace trimmed and offsets shifted by `offset`."""
    for match in _SENTENCE.finditer(text):
        raw = match.group()
        stripped = raw.strip()
        if not stripped:
            continue
        start = match.start() + (len(raw) - len(raw.lstrip()))
        yield offset + start, offset + start + len(stripped), stripped


def _index(references: Sequence[Reference]) -> tuple[list[_Entry], dict[str, list[int]]]:
    entries: list[_Entry] = []
    by_word: dict[str, list[int]] = defaultdict(list)
    for reference in references:
        for _, _, sentence in _sentences(reference.text, 0):
            entry = _Entry(reference=reference, tokens=normalise(sentence), content=content_tokens(sentence))
            for word in entry.content:
                by_word[word].append(len(entries))
            entries.append(entry)
    return entries, by_word


def _best(sentence: str, entries: list[_Entry], by_word: dict[str, list[int]]) -> _Match | None:
    """The reference sentence with the highest token-sequence ratio among those sharing content words."""
    words = normalise(sentence)
    content = content_tokens(sentence)
    if not content:
        return None
    shared: Counter[int] = Counter()
    for word in content:
        shared.update(by_word.get(word, []))
    candidates = sorted((i for i, count in shared.items() if count >= MIN_SHARED_CONTENT), key=lambda i: (-shared[i], i))
    best: _Match | None = None
    for i in candidates[:MAX_CANDIDATES]:
        entry = entries[i]
        matcher = SequenceMatcher(None, words, entry.tokens, autojunk=False)
        ratio = matcher.ratio()
        if best is None or ratio > best.ratio:
            run = matcher.find_longest_match(0, len(words), 0, len(entry.tokens)).size
            overlap = len(content & entry.content) / len(content)
            best = _Match(ratio=ratio, run=run, overlap=overlap, entry=entry)
    return best


def check(paragraphs: Sequence[Paragraph], references: Sequence[Reference]) -> list[dict]:
    """Findings for one document, ordered by position. Each is {start, end, kind, source_id, excerpt_id, score}."""
    entries, by_word = _index(references)
    findings: list[dict] = []
    offset = 0
    for position, paragraph in enumerate(paragraphs):
        if position:
            offset += 1  # the newline that joins paragraphs in the plain text
        if paragraph.quoted:
            # A cited quotation is the author's to attribute, so it is not checked. An uncited one is flagged
            # whether or not a source matches it; a match, when there is one, says which source it came from.
            if not paragraph.cited:
                finding = _quote_finding(paragraph.text, offset, entries, by_word)
                if finding is not None:
                    findings.append(finding)
        else:
            for start, end, sentence in _sentences(paragraph.text, offset):
                if len(normalise(sentence)) < MIN_WORDS:
                    continue
                match = _best(sentence, entries, by_word)
                kind = _classify(match)
                if kind is None:
                    continue
                findings.append({
                    "start": start, "end": end, "kind": kind,
                    "source_id": match.entry.reference.source_id, "excerpt_id": match.entry.reference.excerpt_id,
                    "score": round(match.ratio, 3),
                })
        offset += len(paragraph.text)
    return findings


def _quote_finding(text: str, offset: int, entries: list[_Entry], by_word: dict[str, list[int]]) -> dict | None:
    whole = text.strip()
    if not whole:
        return None
    start = offset + (len(text) - len(text.lstrip()))
    match = _best(whole, entries, by_word)
    matched = match is not None and match.ratio >= PARAPHRASE_RATIO
    return {
        "start": start, "end": start + len(whole), "kind": "quote_without_citation",
        "source_id": match.entry.reference.source_id if matched else None,
        "excerpt_id": match.entry.reference.excerpt_id if matched else None,
        "score": round(match.ratio, 3) if match is not None else 0.0,
    }


def _classify(match: _Match | None) -> str | None:
    if match is None:
        return None
    if match.run >= LONG_RUN or match.ratio >= NEAR_RATIO:
        return "near_verbatim"
    if match.ratio >= PARAPHRASE_RATIO and match.overlap >= PARAPHRASE_OVERLAP:
        return "close_paraphrase"
    return None


def summarise(findings: Sequence[dict]) -> dict[str, int]:
    counts = dict.fromkeys(KINDS, 0)
    for finding in findings:
        counts[finding["kind"]] += 1
    return counts
