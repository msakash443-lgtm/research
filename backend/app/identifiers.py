"""Identifier checks for author and institution disambiguation (spec §6, X.18).

Pure: plain text in, plain text out, no network and no database. An ORCID iD is checked for shape
and its ISO 7064 MOD 11-2 check digit, so a mistyped iD is refused when it is saved rather than
linked to the wrong person later. Checking the shape does not prove the iD belongs to the author;
that needs a person to confirm it.
"""

from __future__ import annotations

import re

_ORCID_PREFIX = re.compile(r"^(?:https?://)?(?:www\.)?orcid\.org/", re.IGNORECASE | re.ASCII)
_ORCID = re.compile(r"^[0-9]{4}-[0-9]{4}-[0-9]{4}-[0-9]{3}[0-9X]$")


def _orcid_check_digit(first_fifteen: str) -> str:
    total = 0
    for char in first_fifteen:
        total = (total + int(char)) * 2
    result = (12 - total % 11) % 11
    return "X" if result == 10 else str(result)


def normalize_orcid(value: str | None) -> str | None:
    """Return a bare ORCID iD ("0000-0002-1825-0097"), or None for blank input.

    Accepts the bare iD or an orcid.org link. Raises ValueError when the text is not shaped like
    an ORCID iD or its check digit is wrong, so a malformed iD is never stored as if it were valid.
    """
    if value is None:
        return None
    text = _ORCID_PREFIX.sub("", value.strip()).strip().rstrip("/").strip().upper()
    if not text:
        return None
    if not _ORCID.match(text) or _orcid_check_digit(text.replace("-", "")[:15]) != text[-1]:
        raise ValueError("ORCID iD must look like 0000-0002-1825-0097 and have a valid check digit")
    return text


_ROR_PREFIX = re.compile(r"^(?:https?://)?(?:www\.)?ror\.org/", re.IGNORECASE | re.ASCII)
_ROR = re.compile(r"^0[0-9a-hjkmnp-tv-z]{6}[0-9]{2}$")
_CROCKFORD = "0123456789abcdefghjkmnpqrstvwxyz"


def _ror_check_digits(first_seven: str) -> str:
    number = 0
    for char in first_seven:
        number = number * 32 + _CROCKFORD.index(char)
    return f"{98 - (number * 100) % 97:02d}"


def normalize_ror(value: str | None) -> str | None:
    """Return the full ROR ID ("https://ror.org/02mhbdp94"), or None for blank input.

    Accepts the full URL, the ror.org form, or the bare nine-character id. Raises ValueError when
    the text is not shaped like a ROR id (leading 0, Crockford base32 without I, L, O, U, two
    digits) or its ISO 7064 MOD 97-10 check digits are wrong.
    """
    if value is None or not value.strip():
        return None
    text = _ROR_PREFIX.sub("", value.strip()).strip().rstrip("/").strip().lower()
    if not _ROR.match(text) or _ror_check_digits(text[:7]) != text[7:]:
        raise ValueError("ROR ID must look like https://ror.org/02mhbdp94 and have valid check digits")
    return f"https://ror.org/{text}"
