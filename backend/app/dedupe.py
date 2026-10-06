"""Duplicate detection for retrieved papers (spec 5.2.2): DOI first, then title + year + first author.

Pure functions, no database. Works on anything with `doi`, `title`, `year` and `authors`
attributes (a `PaperRecord`, a `Source`, a `DedupeRecord`).

Matching rules, deliberately cautious because a wrong merge hides a paper while a missed
duplicate only costs a person a second look:

1. Same normalised DOI -> duplicates.
2. Otherwise the same *work key* -> duplicates: normalised title + year + first author's family
   name. A missing year or author only matches another missing one, and a title shorter than
   `MIN_TITLE_CHARS` after normalisation (e.g. "Introduction") gets no work key at all.
3. Two records with *different* DOIs are never merged, even with an identical work key (a
   preprint and its published version, or a book and its review): a person decides those.

Title normalisation is Unicode-safe: letters and digits of every script are kept (an
"ASCII only" filter would reduce Devanagari or Chinese titles to nothing and make unrelated
papers collide). Case is folded, compatibility forms are unified (NFKC, so fullwidth and ligature
characters match), HTML tags/entities from publisher feeds are removed, punctuation becomes a
space, and combining accents are dropped **only on Latin letters** ("Café" matches "Cafe"), never
on other scripts where the marks carry meaning.
"""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Protocol

from app.doi import normalize_doi

MIN_TITLE_CHARS = 15  # in "weight" units: a CJK/Hangul/Kana character counts double (denser script)
_TAGS = re.compile(r"<[^>]{1,200}>")


class HasWorkFields(Protocol):
    doi: Any
    title: Any
    year: Any
    authors: Any


@dataclass(frozen=True)
class DedupeRecord:
    title: str | None = None
    doi: str | None = None
    year: int | None = None
    authors: tuple[str, ...] | list[str] | None = None


def _is_latin(char: str) -> bool:
    try:
        return unicodedata.name(char).startswith("LATIN")
    except ValueError:
        return False


def _fold(text: str) -> str:
    """NFKC + casefold, then drop combining marks that sit on Latin letters; keep all other marks."""
    out: list[str] = []
    for char in unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", text).casefold()):
        if unicodedata.category(char) == "Mn" and out and _is_latin(out[-1]):
            continue
        out.append(char)
    return unicodedata.normalize("NFC", "".join(out))


def _weight(text: str) -> int:
    """Length where dense scripts count double, so a 10-character Chinese title is as specific as 20 Latin ones."""
    total = 0
    for char in text:
        try:
            dense = unicodedata.name(char).startswith(("CJK", "HIRAGANA", "KATAKANA", "HANGUL"))
        except ValueError:
            dense = False
        total += 2 if dense else 1
    return total


def normalise_title(title: str | None) -> str:
    """The comparison form of a title; "" when nothing comparable is left."""
    if not isinstance(title, str):
        return ""
    text = html.unescape(_TAGS.sub(" ", title))
    kept = []
    for char in _fold(text):
        category = unicodedata.category(char)
        # Letters, digits and combining marks (needed by Indic scripts) stay; everything else separates words.
        kept.append(char if category[0] in "LN" or category in ("Mn", "Mc") else " ")
    return " ".join("".join(kept).split())


def first_author_key(authors: Iterable[str] | None) -> str:
    """The first author's family name in comparison form; "" if there is none.

    "Lovelace, Ada", "Ada Lovelace" and "A. Lovelace" agree, and so do "Garcia Lopez, M." and
    "Maria Garcia Lopez" (the key is the last word of the family name).
    """
    if not authors:
        return ""
    first = next((a for a in authors if isinstance(a, str) and a.strip()), "")
    if not first:
        return ""
    first = _TAGS.sub(" ", first)
    family = first.split(",", 1)[0] if "," in first else first
    tokens = normalise_title(family).split()  # same Unicode-safe folding as titles
    # The last word of the family name, so "Garcia Lopez, M." and "Maria Garcia Lopez" agree.
    return tokens[-1] if tokens else ""


def doi_key(doi: Any) -> str | None:
    if not isinstance(doi, str):
        return None
    try:
        return normalize_doi(doi)
    except ValueError:
        return None


def work_key(record: HasWorkFields) -> str | None:
    """`title|year|first-author`, or None when the title is too short to identify a work."""
    title = normalise_title(record.title)
    if _weight(title) < MIN_TITLE_CHARS:
        return None
    year = record.year if isinstance(record.year, int) and not isinstance(record.year, bool) else ""
    return f"{title}|{year}|{first_author_key(record.authors)}"


def match_kind(a: HasWorkFields, b: HasWorkFields) -> str | None:
    """"doi", "title_year_author", or None. Different DOIs never match."""
    doi_a, doi_b = doi_key(a.doi), doi_key(b.doi)
    if doi_a and doi_b:
        return "doi" if doi_a == doi_b else None
    key_a = work_key(a)
    if key_a is not None and key_a == work_key(b):
        return "title_year_author"
    return None


def group_duplicates(records: Iterable[HasWorkFields]) -> list[list[int]]:
    """Cluster `records` into duplicate groups; returns lists of input indices, in input order.

    The first record of a group is its anchor. A record joins the first earlier group it matches
    that does not already hold a different DOI, so a chain A~B (title) and B~C (title) cannot pull
    two papers with conflicting DOIs into one group.
    """
    groups: list[list[int]] = []
    group_dois: list[set[str]] = []
    by_doi: dict[str, int] = {}
    by_key: dict[str, list[int]] = {}
    for index, record in enumerate(records):
        doi, key = doi_key(record.doi), work_key(record)
        target = None
        if doi and doi in by_doi:
            target = by_doi[doi]
        elif key is not None:
            for candidate in by_key.get(key, ()):
                if not doi or not group_dois[candidate] or doi in group_dois[candidate]:
                    target = candidate
                    break
        if target is None:
            target = len(groups)
            groups.append([])
            group_dois.append(set())
        groups[target].append(index)
        if doi:
            group_dois[target].add(doi)
            by_doi.setdefault(doi, target)
        if key is not None and target not in by_key.setdefault(key, []):
            by_key[key].append(target)
    return groups
