"""Check whether a cited quotation occurs in its stored PDF or excerpt."""

from app.config import get_settings
from app.fulltext import FullText, FullTextError, Page, extract_pdf_text
from app.models import Source, SourceExcerpt
from app.object_storage import ObjectStorageError, get_object_store


def quote_matches_source(source: Source, excerpt: SourceExcerpt, quote: str) -> bool:
    """A stored PDF takes precedence; a source without one uses its write-once excerpt."""
    if not quote.strip():
        return False
    if source.fulltext_path:
        try:
            settings = get_settings()
            pdf = get_object_store(settings).get(source.fulltext_path)
            fulltext = extract_pdf_text(pdf, max_pages=settings.fulltext_max_pages)
        except (ObjectStorageError, FullTextError):
            return False
    else:
        fulltext = FullText(text=excerpt.content,
                            pages=(Page(number=1, label=None, start=0, end=len(excerpt.content)),),
                            sections=(), empty_pages=(), sha256=excerpt.content_hash)
    return bool(fulltext.find_quote(quote))
