"""Discipline profiles: how a field's methods, databases and norms are configured (spec principle 6, "config, not code").

A profile is a file in `app/profiles/` (TOML). A project names one (`Project.discipline`) and may override
individual settings (`Project.config_json`). Every value is validated, so a typo ("pubmd", "PRISMA2020")
is rejected when it is saved, not discovered when a search runs.

Effective settings = the profile's values, with any setting the project has set replacing the profile's
(a list replaces the whole list; it is not merged). The profile name and version are reported with the
effective settings so work can later record exactly which configuration it used.

The shipped profiles are the disciplines the product owner chose (plan Q3, Decisions log 2026-10-09).
Their values are defaults; a project can override any setting.
"""

from __future__ import annotations

import re
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

PROFILE_DIR = Path(__file__).resolve().parent / "profiles"

# Extend these as connectors and guidelines are added; an unknown id fails early instead of silently doing nothing.
KNOWN_DATABASES = frozenset({
    "openalex", "crossref", "semantic_scholar", "arxiv", "unpaywall", "pubmed", "europe_pmc", "core",
    "opencitations", "scopus", "web_of_science", "ieee_xplore", "econlit", "repec",
    "psycinfo", "ssrn", "jstor", "business_source_complete", "mla_international_bibliography",
})
KNOWN_REPORTING_GUIDELINES = frozenset({
    "PRISMA-2020", "PRISMA-ScR", "PRISMA-P", "MOOSE", "STROBE", "CONSORT", "COREQ", "SRQR", "ENTREQ", "CHEERS",
})
_ID = re.compile(r"^[a-z][a-z0-9_]{1,59}$")  # methods and similar free-form ids
_CSL_STYLE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")  # CSL style ids: "apa", "chicago-author-date", ...


class ProfileError(RuntimeError):
    pass


def _unique(values: list[str], what: str) -> list[str]:
    if len(set(values)) != len(values):
        raise ValueError(f"{what} contains duplicates")
    return values


class ProfileSettings(BaseModel):
    """The four settings the spec names. `None` means "not set" (a project override leaves it to the profile)."""

    model_config = ConfigDict(extra="forbid")

    citation_style: str | None = Field(default=None, max_length=80)
    databases: list[str] | None = Field(default=None, max_length=20)
    methods: list[str] | None = Field(default=None, max_length=30)
    reporting_guideline: str | None = None

    @field_validator("citation_style")
    @classmethod
    def _style(cls, value: str | None) -> str | None:
        if value is not None and not _CSL_STYLE.match(value):
            raise ValueError("citation_style must be a CSL style id such as 'apa' or 'chicago-author-date'")
        return value

    @field_validator("databases")
    @classmethod
    def _databases(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        unknown = sorted(set(value) - KNOWN_DATABASES)
        if unknown:
            raise ValueError(f"unknown database(s) {unknown}; known: {sorted(KNOWN_DATABASES)}")
        return _unique(value, "databases")

    @field_validator("methods")
    @classmethod
    def _methods(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        bad = [m for m in value if not _ID.match(m)]
        if bad:
            raise ValueError(f"method ids must be lower_snake_case words, got {bad}")
        return _unique(value, "methods")

    @field_validator("reporting_guideline")
    @classmethod
    def _guideline(cls, value: str | None) -> str | None:
        if value is not None and value not in KNOWN_REPORTING_GUIDELINES:
            raise ValueError(f"unknown reporting guideline {value!r}; known: {sorted(KNOWN_REPORTING_GUIDELINES)}")
        return value


class Profile(BaseModel):
    name: str
    version: int
    label: str
    description: str
    settings: ProfileSettings


class EffectiveSettings(BaseModel):
    """What a project actually uses: the profile's values with the project's overrides applied."""

    profile: str | None
    profile_version: int | None
    citation_style: str | None
    databases: list[str]
    methods: list[str]
    reporting_guideline: str | None


@lru_cache(maxsize=None)
def _load(directory: str, name: str) -> Profile:
    path = Path(directory) / f"{name}.toml"
    if not _ID.match(name) or not path.is_file():
        raise ProfileError(f"Discipline profile {name!r} does not exist")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        profile = Profile(
            name=data.get("name"), version=data.get("version"), label=data.get("label"),
            description=data.get("description"), settings=ProfileSettings(**data.get("settings", {})),
        )
    except Exception as exc:  # TOML syntax, missing keys, or a setting that fails validation
        raise ProfileError(f"Discipline profile {path.name} is invalid: {exc}") from exc
    if profile.name != name:
        raise ProfileError(f"Discipline profile {path.name} declares name {profile.name!r}")
    if profile.version < 1:
        raise ProfileError(f"Discipline profile {path.name} needs a version of 1 or more")
    return profile


def load_profile(name: str) -> Profile:
    return _load(str(PROFILE_DIR), name)


def available_profiles() -> list[str]:
    return sorted(path.stem for path in PROFILE_DIR.glob("*.toml"))


def effective_settings(discipline: str | None, overrides: dict[str, Any] | None) -> EffectiveSettings:
    """The profile's settings with the project's overrides on top. Raises ProfileError for an unknown profile."""
    profile = load_profile(discipline) if discipline else None
    base = profile.settings.model_dump() if profile else {}
    # Re-validate the stored overrides: config written under older rules must still be coherent.
    chosen = {k: v for k, v in ProfileSettings(**(overrides or {})).model_dump().items() if v is not None}
    merged = {**{k: v for k, v in base.items() if v is not None}, **chosen}
    return EffectiveSettings(
        profile=profile.name if profile else None,
        profile_version=profile.version if profile else None,
        citation_style=merged.get("citation_style"),
        databases=merged.get("databases", []),
        methods=merged.get("methods", []),
        reporting_guideline=merged.get("reporting_guideline"),
    )
