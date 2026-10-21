"""Privacy profiles: jurisdiction, retention defaults and hosting-region rules as config (X.4.10; spec principle 6).

A profile is a TOML file in `app/privacy_profiles/`, chosen for the whole installation with `PRIVACY_PROFILE`
(unlike a discipline profile, which belongs to a project: retention and hosting are installation-wide).
Every value is validated, so a typo fails at start-up, not when a purge or an export runs.

Effective settings = the explicit environment setting where one is given, else the profile's value.
`allowed_regions` empty means any hosting region is fine; when it is not empty, production start-up refuses
unless `DATA_RESIDENCY_REGION` names one of them. Nothing here is legal advice, and no shipped profile picks a
retention period for the owner (Decisions log 2026-10-19: kept until the owner deletes it).
"""

from __future__ import annotations

import re
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

PROFILE_DIR = Path(__file__).resolve().parent / "privacy_profiles"
_NAME = re.compile(r"^[a-z][a-z0-9_]{1,59}$")
_REGION = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")


class PrivacyProfileError(RuntimeError):
    pass


class PrivacySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    regulation: str | None = Field(default=None, max_length=80)
    # How long the law gives the owner to answer an access/erasure request; shown to people, not enforced.
    data_subject_request_days: int | None = Field(default=None, ge=1, le=365)
    # Default for RESEARCH_RUN_SNAPSHOT_RETENTION_DAYS when that setting is not given; None = kept.
    run_snapshot_retention_days: int | None = Field(default=None, ge=1)
    # Hosting regions allowed for the database and object store; empty = unrestricted.
    allowed_regions: list[str] = Field(default_factory=list, max_length=30)


class PrivacyProfile(BaseModel):
    name: str
    version: int
    label: str
    description: str
    settings: PrivacySettings


@lru_cache(maxsize=None)
def _load(directory: str, name: str) -> PrivacyProfile:
    path = Path(directory) / f"{name}.toml"
    if not _NAME.match(name) or not path.is_file():
        raise PrivacyProfileError(f"Privacy profile {name!r} does not exist")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        profile = PrivacyProfile(
            name=data.get("name"), version=data.get("version"), label=data.get("label"),
            description=data.get("description"), settings=PrivacySettings(**data.get("settings", {})),
        )
    except Exception as exc:  # TOML syntax, missing keys, or a setting that fails validation
        raise PrivacyProfileError(f"Privacy profile {path.name} is invalid: {exc}") from exc
    if profile.name != name:
        raise PrivacyProfileError(f"Privacy profile {path.name} declares name {profile.name!r}")
    if profile.version < 1:
        raise PrivacyProfileError(f"Privacy profile {path.name} needs a version of 1 or more")
    bad = [r for r in profile.settings.allowed_regions if not _REGION.match(r)]
    if bad:
        raise PrivacyProfileError(f"Privacy profile {path.name} has invalid region id(s) {bad}")
    return profile


def load_privacy_profile(name: str, directory: Path | None = None) -> PrivacyProfile:
    return _load(str(directory or PROFILE_DIR), name)


def available_privacy_profiles() -> list[str]:
    return sorted(path.stem for path in PROFILE_DIR.glob("*.toml"))


def residency_problem(profile: PrivacyProfile, region: str | None) -> str | None:
    """Why `region` breaks the profile's hosting rule, or None when it is fine."""
    allowed = profile.settings.allowed_regions
    if not allowed:
        return None
    if not region:
        return f"privacy profile {profile.name!r} limits hosting to {allowed}; set DATA_RESIDENCY_REGION"
    if region not in allowed:
        return f"DATA_RESIDENCY_REGION {region!r} is not allowed by privacy profile {profile.name!r} (allowed: {allowed})"
    return None


def effective_privacy(settings: Any) -> dict[str, Any]:
    """The profile's values with explicit settings on top; reports which profile and version were used."""
    profile = load_privacy_profile(settings.privacy_profile)
    s = profile.settings
    retention = settings.research_run_snapshot_retention_days
    return {
        "profile": profile.name,
        "profile_version": profile.version,
        "label": profile.label,
        "description": profile.description,
        "regulation": s.regulation,
        "data_subject_request_days": s.data_subject_request_days,
        "run_snapshot_retention_days": retention if retention is not None else s.run_snapshot_retention_days,
        "allowed_regions": list(s.allowed_regions),
        "data_residency_region": settings.data_residency_region,
    }
