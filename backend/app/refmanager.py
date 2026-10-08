"""BibTeX and RIS reading and writing (M1.11.1). Pure text in, plain data out: no DB, no network.

A parsed record uses the `SourceCreate` field names (title, authors, year, doi, url, venue,
abstract, source_type). An entry that cannot become a source (no title) is reported in
`skipped`, never silently dropped or filled in.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

MAX_ENTRIES = 2000

_BIBTEX_TYPES = {
    "article": "article", "inproceedings": "conference", "conference": "conference", "proceedings": "conference",
    "book": "book", "incollection": "chapter", "inbook": "chapter", "phdthesis": "thesis", "mastersthesis": "thesis",
    "techreport": "report", "misc": "other", "unpublished": "preprint", "online": "other",
}
_BIBTEX_OUT = {"article": "article", "conference": "inproceedings", "book": "book", "chapter": "incollection",
               "thesis": "phdthesis", "report": "techreport", "preprint": "unpublished"}
_RIS_TYPES = {"JOUR": "article", "CONF": "conference", "CPAPER": "conference", "BOOK": "book", "CHAP": "chapter",
              "THES": "thesis", "RPRT": "report", "UNPB": "preprint", "GEN": "other"}
_RIS_OUT = {"article": "JOUR", "conference": "CONF", "book": "BOOK", "chapter": "CHAP", "thesis": "THES",
            "report": "RPRT", "preprint": "UNPB"}
_YEAR = re.compile(r"(?<!\d)(1[0-9]{3}|2[0-9]{3})(?!\d)")


@dataclass
class ParseResult:
    records: list[dict] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _year(text: str | None) -> int | None:
    match = _YEAR.search(text or "")
    return int(match.group(1)) if match else None


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", value.replace("{", "").replace("}", "")).strip()
    return text or None


def _record(title, authors, year, doi, url, venue, abstract, source_type) -> dict:
    record = {"title": title, "authors": authors or None, "year": year, "doi": doi, "url": url,
              "venue": venue, "abstract": abstract, "source_type": source_type or "article"}
    return {key: value for key, value in record.items() if value is not None}


def _read_braced(text: str, i: int) -> tuple[str, int]:
    depth, start = 0, i
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : i], i + 1
        i += 1
    return text[start + 1 :], len(text)


def _read_entry(text: str, i: int) -> tuple[str, int]:
    """An entry body opened by `{` or `(` at `i`. A `(` entry closes at the first `)` outside any
    braced or quoted value, so `title = {A (B)}` doesn't end it early."""
    if text[i] == "{":
        return _read_braced(text, i)
    depth, quoted, j = 0, False, i + 1
    while j < len(text):
        c = text[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth = max(depth - 1, 0)
        elif c == '"' and depth == 0 and text[j - 1] != "\\":
            quoted = not quoted
        elif c == ")" and depth == 0 and not quoted:
            return text[i + 1 : j], j + 1
        j += 1
    return text[i + 1 :], len(text)


def _read_value(text: str, i: int) -> tuple[str, int]:
    while i < len(text) and text[i].isspace():
        i += 1
    if i < len(text) and text[i] == "{":
        return _read_braced(text, i)
    if i < len(text) and text[i] == '"':
        j = i + 1
        while j < len(text) and not (text[j] == '"' and text[j - 1] != "\\"):
            j += 1
        return text[i + 1 : j], j + 1
    j = i
    while j < len(text) and text[j] not in ",}\n":
        j += 1
    return text[i:j].strip(), j


def parse_bibtex(text: str) -> ParseResult:
    result = ParseResult()
    i = 0
    while True:
        at = text.find("@", i)
        if at == -1 or len(result.records) + len(result.skipped) >= MAX_ENTRIES:
            break
        match = re.compile(r"@\s*([A-Za-z]+)\s*([{(])").match(text, at)
        if not match:
            i = at + 1
            continue
        kind = match.group(1).lower()
        if kind in {"comment", "string", "preamble"}:
            _, i = _read_entry(text, match.end() - 1)
            continue
        body, i = _read_entry(text, match.end() - 1)
        key, _, rest = body.partition(",")
        fields: dict[str, str] = {}
        pos = 0
        while pos < len(rest):
            m = re.compile(r"\s*([A-Za-z][A-Za-z0-9_-]*)\s*=").match(rest, pos)
            if not m:
                pos += 1
                continue
            value, pos = _read_value(rest, m.end())
            fields[m.group(1).lower()] = value
        title = _clean(fields.get("title"))
        if not title:
            result.skipped.append(f"{key.strip() or 'entry'}: no title")
            continue
        authors = [a for a in (_clean(a) for a in re.split(r"\s+and\s+", fields.get("author", ""))) if a]
        doi = _clean(fields.get("doi"))
        url = _clean(fields.get("url"))
        venue = _clean(fields.get("journal") or fields.get("booktitle") or fields.get("publisher"))
        result.records.append(_record(title, authors, _year(fields.get("year")), doi, url, venue,
                                      _clean(fields.get("abstract")), _BIBTEX_TYPES.get(kind, "other")))
    return result


def parse_ris(text: str) -> ParseResult:
    result = ParseResult()
    entry: dict[str, list[str]] = {}

    def flush() -> None:
        nonlocal entry
        if not entry:
            return
        first = lambda *tags: next((_clean(v[0]) for t in tags if (v := entry.get(t))), None)  # noqa: E731
        title = first("TI", "T1")
        if not title:
            result.skipped.append(f"entry {len(result.records) + len(result.skipped) + 1}: no title")
        else:
            authors = [a for a in (_clean(a) for t in ("AU", "A1") for a in entry.get(t, [])) if a]
            result.records.append(_record(
                title, authors, _year(first("PY", "Y1", "DA")), first("DO"), first("UR"),
                first("JO", "JF", "T2", "JA"), first("AB", "N2"), _RIS_TYPES.get((first("TY") or "").upper(), "other")))
        entry = {}

    for line in text.splitlines():
        m = re.match(r"^([A-Z][A-Z0-9])\s{1,2}-\s?(.*)$", line)
        if not m:
            continue
        tag, value = m.group(1), m.group(2).strip()
        if tag == "TY":
            flush()
            if len(result.records) + len(result.skipped) >= MAX_ENTRIES:
                return result
        if tag == "ER":
            flush()
        elif value:
            entry.setdefault(tag, []).append(value)
    flush()
    return result


def _bib_escape(value: str) -> str:
    return value.replace("\\", " ").replace("{", "(").replace("}", ")")


def to_bibtex(records: list[dict]) -> str:
    out: list[str] = []
    used: set[str] = set()
    for rec in records:
        first_author = (rec.get("authors") or ["anon"])[0].split(",")[0].split()[-1:] or ["anon"]
        base = re.sub(r"[^A-Za-z0-9]", "", first_author[0]).lower() or "anon"
        key = f"{base}{rec.get('year') or ''}"
        while key in used:
            key += "a"
        used.add(key)
        kind = _BIBTEX_OUT.get(rec.get("source_type", ""), "misc")
        lines = [f"@{kind}{{{key},", f"  title = {{{_bib_escape(rec['title'])}}},"]
        if rec.get("authors"):
            lines.append(f"  author = {{{_bib_escape(' and '.join(rec['authors']))}}},")
        if rec.get("year"):
            lines.append(f"  year = {{{rec['year']}}},")
        if rec.get("venue"):
            lines.append(f"  {'booktitle' if kind == 'inproceedings' else 'journal'} = {{{_bib_escape(rec['venue'])}}},")
        for name in ("doi", "url"):
            if rec.get(name):
                lines.append(f"  {name} = {{{_bib_escape(rec[name])}}},")
        lines.append("}")
        out.append("\n".join(lines))
    return "\n\n".join(out) + ("\n" if out else "")


def to_ris(records: list[dict]) -> str:
    out: list[str] = []
    for rec in records:
        lines = [f"TY  - {_RIS_OUT.get(rec.get('source_type', ''), 'GEN')}", f"TI  - {_one_line(rec['title'])}"]
        lines += [f"AU  - {_one_line(a)}" for a in rec.get("authors") or []]
        if rec.get("year"):
            lines.append(f"PY  - {rec['year']}")
        if rec.get("venue"):
            lines.append(f"JO  - {_one_line(rec['venue'])}")
        for tag, name in (("DO", "doi"), ("UR", "url"), ("AB", "abstract")):
            if rec.get(name):
                lines.append(f"{tag}  - {_one_line(rec[name])}")
        lines.append("ER  - ")
        out.append("\n".join(lines))
    return "\n".join(out) + ("\n" if out else "")


def _one_line(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()
