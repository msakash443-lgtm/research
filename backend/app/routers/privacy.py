from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.config import get_settings
from app.dependencies import current_user
from app.models import User
from app.privacy_profile import effective_privacy
from app.processors import NOTICE, processor_register

# Installation-wide, not nested under a project (like /guide).
router = APIRouter(prefix="/privacy", tags=["privacy"])


@router.get("/processors")
def get_processors(_user: User = Depends(current_user)) -> dict[str, Any]:
    """The in-app privacy notice and the outside services currently switched on (X.4.9). Hosts only, no credentials."""
    return {"notice": NOTICE, "processors": processor_register(get_settings())}


@router.get("/profile")
def get_privacy_profile(_user: User = Depends(current_user)) -> dict[str, Any]:
    """The installation's privacy profile and the retention/hosting values in force (X.4.10)."""
    return effective_privacy(get_settings())
