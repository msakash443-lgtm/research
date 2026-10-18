"""CRediT (Contributor Roles Taxonomy) support for manuscript author contributions (M6.6, spec Sec5.11.6).

Pure, no DB/HTTP: the 14 standard CRediT roles (https://credit.niso.org/) and a validator used by
`routers/integrity.py` before a role list is stored on a `ProjectMember`. Only the named human members of a
project may hold CRediT roles — AI is never a contributor here, matching the plan rule "AI never listed as
an author"; the caller enforces that by only ever calling this for a `ProjectMember` row (a user).
"""

from __future__ import annotations

# Definition order is the taxonomy's own published order.
CREDIT_ROLES: tuple[str, ...] = (
    "Conceptualization",
    "Data curation",
    "Formal analysis",
    "Funding acquisition",
    "Investigation",
    "Methodology",
    "Project administration",
    "Resources",
    "Software",
    "Supervision",
    "Validation",
    "Visualization",
    "Writing - original draft",
    "Writing - review & editing",
)

_VALID = set(CREDIT_ROLES)


class InvalidCreditRole(ValueError):
    """Raised when a role outside the 14 standard CRediT terms is supplied."""


def validate_roles(roles: list[str]) -> list[str]:
    """Normalises a role list: rejects unknown terms, drops duplicates, keeps taxonomy order.

    Raises `InvalidCreditRole` naming every unknown term at once, rather than failing on the first one.
    """
    unknown = sorted({r for r in roles if r not in _VALID})
    if unknown:
        raise InvalidCreditRole(f"Not a CRediT role: {', '.join(unknown)}")
    chosen = set(roles)
    return [role for role in CREDIT_ROLES if role in chosen]
