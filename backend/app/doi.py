import re

_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)
_DOI = re.compile(r"^10\.\d{4,9}/\S+$")
MAX_DOI_LENGTH = 255


def normalize_doi(value: str | None) -> str | None:
    """Return a bare, lower-cased DOI ("10.1000/xyz"), or None for blank input.

    Raises ValueError when the text is not shaped like a DOI, so a malformed value is
    never stored as if it were a verified identifier.
    """
    if value is None:
        return None
    text = _PREFIX.sub("", value.strip()).strip().lower()
    if not text:
        return None
    if len(text) > MAX_DOI_LENGTH or not _DOI.match(text):
        raise ValueError("DOI must look like 10.1234/abc (a bare DOI or a doi.org link)")
    return text
