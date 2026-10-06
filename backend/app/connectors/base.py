"""Common interface for scholarly connectors (spec section 6, "Integration rules").

A connector turns one external service (OpenAlex, Crossref, ...) into `PaperRecord`s. Rules:

* Everything a connector returns is untrusted third-party data. `PaperRecord` never carries
  instructions, and its text fields must be fenced before reaching a model (M1.2).
* A connector never writes to the database; callers decide what to store, and anything stored
  from a connector is unverified until a person verifies it (plan rule 24).
* A capability a service lacks raises `NotSupportedError`. It never returns an empty result
  that could be mistaken for "no citations exist" (plan rule 22). A failed call raises
  `ConnectorError`; connectors do not swallow errors or invent records.
* Retries, rate limits, caching and ToS flags are not part of this protocol (M1.3.2-M1.3.4).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.doi import normalize_doi

MAX_PAGE_SIZE = 200


class ConnectorError(RuntimeError):
    """The external service could not be queried or returned something unusable."""


class NotSupportedError(ConnectorError):
    """This connector does not offer the requested capability."""


def _http_url(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    value = value.strip()
    if urlsplit(value).scheme not in ("http", "https"):
        raise ValueError("URL must be http or https")
    return value


class PaperRecord(BaseModel):
    """One work, normalised across connectors (spec section 4 `Paper`, minus system-owned fields)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    connector: str = Field(min_length=1, max_length=50)  # which connector produced it
    external_id: str = Field(min_length=1, max_length=255)  # id within that service (e.g. "W123")
    title: str = Field(min_length=1, max_length=500)
    doi: str | None = None  # bare, lower-case; malformed values are rejected, not stored
    authors: tuple[str, ...] = ()
    year: int | None = Field(default=None, ge=1000, le=2200)
    venue: str | None = Field(default=None, max_length=500)
    abstract: str | None = Field(default=None, max_length=20000)
    url: str | None = None
    oa_url: str | None = None
    source_ids: dict[str, str] = Field(default_factory=dict)  # e.g. {"openalex": "W1", "pmid": "9"}
    quality_flags: tuple[str, ...] = ()  # e.g. ("preprint", "retracted")

    @field_validator("doi")
    @classmethod
    def _doi(cls, value: str | None) -> str | None:
        return normalize_doi(value)

    @field_validator("url", "oa_url")
    @classmethod
    def _urls(cls, value: str | None) -> str | None:
        return _http_url(value)


class FullText(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_url: str
    text: str = Field(min_length=1)
    license: str | None = None  # as reported by the service; check before storing/processing (spec section 6)

    @field_validator("source_url")
    @classmethod
    def _url(cls, value: str) -> str:
        checked = _http_url(value)
        if checked is None:
            raise ValueError("source_url is required")
        return checked


class SearchRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    query: str = Field(min_length=1, max_length=2000)  # already in the service's own syntax (M1.5.2)
    limit: int = Field(default=25, ge=1, le=MAX_PAGE_SIZE)
    cursor: str | None = None  # opaque, from the previous page's `next_cursor`
    filters: dict[str, Any] = Field(default_factory=dict)  # connector-specific, e.g. {"year_from": 2015}


class SearchPage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    records: tuple[PaperRecord, ...]
    total: int | None = Field(default=None, ge=0)  # the service's own count; None if it doesn't say
    next_cursor: str | None = None  # None means no further pages


@runtime_checkable
class Connector(Protocol):
    name: str

    def search(self, request: SearchRequest) -> SearchPage: ...

    def get_by_id(self, external_id: str) -> PaperRecord | None:
        """The record, or None when the service says it does not exist. Other failures raise."""
        ...

    def get_citations(self, external_id: str) -> tuple[PaperRecord, ...]:
        """Works that cite this one."""
        ...

    def get_references(self, external_id: str) -> tuple[PaperRecord, ...]:
        """Works this one cites."""
        ...

    def get_fulltext(self, external_id: str) -> FullText | None:
        """Open-access full text, or None when none is available."""
        ...


class ConnectorBase:
    """Optional base: every capability raises `NotSupportedError` until a connector overrides it."""

    name: str = ""

    def _unsupported(self, what: str) -> NotSupportedError:
        return NotSupportedError(f"{self.name or type(self).__name__} does not support {what}")

    def search(self, request: SearchRequest) -> SearchPage:
        raise self._unsupported("search")

    def get_by_id(self, external_id: str) -> PaperRecord | None:
        raise self._unsupported("lookup by id")

    def get_citations(self, external_id: str) -> tuple[PaperRecord, ...]:
        raise self._unsupported("citations")

    def get_references(self, external_id: str) -> tuple[PaperRecord, ...]:
        raise self._unsupported("references")

    def get_fulltext(self, external_id: str) -> FullText | None:
        raise self._unsupported("full text")
