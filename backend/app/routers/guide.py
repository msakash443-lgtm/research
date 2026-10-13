from __future__ import annotations

from fastapi import APIRouter, Depends

from app.dependencies import current_user
from app.guidance import Guide, load_guide
from app.models import User

# Shared catalogue, not nested under a project (like /discipline-profiles).
router = APIRouter(prefix="/guide", tags=["guide"])


@router.get("", response_model=Guide)
def get_guide(_user: User = Depends(current_user)):
    """What each stage, gate and exit condition means, the review types and the synthesis methods (advice only)."""
    return load_guide()
