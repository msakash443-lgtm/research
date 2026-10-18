"""CSL-id-keyed citation/reference formatting (M6.5, spec §5.11.5).

Not a full CSL (Citation Style Language) processor — there is no CSL-JSON/CSL-XML engine in this
repo and none is added here (plan rule: use the standard library/existing dependencies first). This
is a small, discipline-profile-aligned registry: it formats the same `CitedSource` data (from
`app.manuscript_export`) differently per style id. It covers the ids the shipped discipline profiles
already name (`apa`, `modern-language-association` — see `app/profiles/*.toml`) plus the numbered
family the spec names alongside them ("APA, Harvard, Chicago, IEEE, etc.", spec §5.11.5): `vancouver`
(and `ieee`, its common alias). An unset or unrecognised style id falls back to `DEFAULT_STYLE`, the
plain author-year format `app.manuscript_export` used before M6.5 — so a project with no discipline
set (or a discipline whose profile sets no `citation_style`) renders exactly as before.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol


class Source(Protocol):
    """Structural type: anything shaped like `app.manuscript_export.CitedSource`."""

    title: str
    authors: tuple[str, ...]
    year: int | None
    venue: str | None
    doi: str | None


DEFAULT_STYLE = "default"


def _surname(author: str) -> str:
    """"Smith, Jane" -> Smith; "Jane Smith" -> Smith."""
    if "," in author:
        return author.split(",")[0].strip() or author.strip()
    words = author.split()
    return words[-1] if words else author.strip()


def _doi_url(doi: str | None) -> str | None:
    return f"https://doi.org/{doi}" if doi else None


def _join_authors_ampersand(authors: tuple[str, ...]) -> str:
    if not authors:
        return "Anonymous"
    if len(authors) == 1:
        return authors[0]
    return "; ".join(authors[:-1]) + f", & {authors[-1]}"


@dataclass(frozen=True)
class CslStyle:
    id: str
    numbered: bool  # True: in-text marker is "[n]" and references are ordered by first appearance, not alphabetically
    label: Callable[[Source, int], str]  # (source, 1-based position in the reference list) -> in-text marker
    reference: Callable[[Source, int], str]  # (source, 1-based position in the reference list) -> bibliography line


# ---------------------------------------------------------------- default (unchanged since before M6.5)


def _default_label(source: Source, index: int) -> str:
    surname = _surname(source.authors[0]) if source.authors else source.title
    return f"({surname} {source.year})" if source.year else f"({surname})"


def _default_reference(source: Source, index: int) -> str:
    authors = "; ".join(source.authors) if source.authors else "Anonymous"
    year = f" ({source.year})" if source.year else ""
    parts = [f"{authors}{year}. {source.title.rstrip('.')}."]
    if source.venue:
        parts.append(f"{source.venue.rstrip('.')}.")
    doi = _doi_url(source.doi)
    if doi:
        parts.append(doi)
    return " ".join(parts)


# ---------------------------------------------------------------- apa


def _apa_label(source: Source, index: int) -> str:
    surname = _surname(source.authors[0]) if source.authors else source.title
    return f"({surname}, {source.year})" if source.year else f"({surname}, n.d.)"


def _apa_reference(source: Source, index: int) -> str:
    authors = _join_authors_ampersand(source.authors)
    year = f"({source.year})" if source.year else "(n.d.)"
    parts = [f"{authors} {year}. {source.title.rstrip('.')}."]
    if source.venue:
        parts.append(f"{source.venue.rstrip('.')}.")
    doi = _doi_url(source.doi)
    if doi:
        parts.append(doi)
    return " ".join(parts)


# ---------------------------------------------------------------- chicago-author-date


def _chicago_label(source: Source, index: int) -> str:
    surname = _surname(source.authors[0]) if source.authors else source.title
    return f"({surname} {source.year})" if source.year else f"({surname} n.d.)"


def _chicago_reference(source: Source, index: int) -> str:
    authors = "; ".join(source.authors) if source.authors else "Anonymous"
    year = f"{source.year}" if source.year else "n.d."
    title = source.title.rstrip(".")
    parts = [f'{authors}. {year}. "{title}."']
    if source.venue:
        parts.append(f"{source.venue.rstrip('.')}.")
    doi = _doi_url(source.doi)
    if doi:
        parts.append(doi)
    return " ".join(parts)


# ---------------------------------------------------------------- modern-language-association (works-cited)
# MLA is an author-page style; no page/locator is stored against a citation here, so the in-text marker
# is author-only (spec §5.11.5 names the style family; page numbers are out of scope for this export).


def _mla_label(source: Source, index: int) -> str:
    surname = _surname(source.authors[0]) if source.authors else source.title
    return f"({surname})"


def _mla_reference(source: Source, index: int) -> str:
    authors = "; ".join(source.authors) if source.authors else "Anonymous"
    title = source.title.rstrip(".")
    parts = [f'{authors}. "{title}."']
    if source.venue:
        year = f", {source.year}" if source.year else ""
        parts.append(f"{source.venue.rstrip('.')}{year}.")
    elif source.year:
        parts.append(f"{source.year}.")
    doi = _doi_url(source.doi)
    if doi:
        parts.append(f"{doi}.")
    return " ".join(parts)


# ---------------------------------------------------------------- vancouver / ieee (numbered)


def _vancouver_label(source: Source, index: int) -> str:
    return f"[{index}]"


def _vancouver_reference(source: Source, index: int) -> str:
    authors = ", ".join(source.authors) if source.authors else "Anonymous"
    title = source.title.rstrip(".")
    bits = [f"{index}. {authors}. {title}."]
    if source.venue:
        bits.append(f"{source.venue.rstrip('.')}.")
    if source.year:
        bits.append(f"{source.year}.")
    doi = _doi_url(source.doi)
    if doi:
        bits.append(doi)
    return " ".join(bits)


_STYLES: dict[str, CslStyle] = {
    DEFAULT_STYLE: CslStyle(DEFAULT_STYLE, False, _default_label, _default_reference),
    "apa": CslStyle("apa", False, _apa_label, _apa_reference),
    "chicago-author-date": CslStyle("chicago-author-date", False, _chicago_label, _chicago_reference),
    "modern-language-association": CslStyle("modern-language-association", False, _mla_label, _mla_reference),
    "mla": CslStyle("mla", False, _mla_label, _mla_reference),
    "vancouver": CslStyle("vancouver", True, _vancouver_label, _vancouver_reference),
    "ieee": CslStyle("ieee", True, _vancouver_label, _vancouver_reference),
}


def style_for(style_id: str | None) -> CslStyle:
    """The named style, or `DEFAULT_STYLE` for `None` or an id this registry does not carry."""
    return _STYLES.get(style_id or DEFAULT_STYLE, _STYLES[DEFAULT_STYLE])


def known_styles() -> list[str]:
    return sorted(_STYLES)
