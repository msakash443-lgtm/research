from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.config import Settings, get_settings
from app.connectors.access import CATALOGUE, AccessKind, is_enabled
from app.dependencies import current_user
from app.models import User

router = APIRouter(prefix="/connectors", tags=["connectors"])


class ConnectorRead(BaseModel):
    name: str
    label: str
    access: AccessKind
    enabled: bool  # switched on by configuration
    implemented: bool  # a connector for it exists
    usable: bool  # enabled and implemented
    note: str


@router.get("", response_model=list[ConnectorRead])
def list_connectors(_user: User = Depends(current_user), settings: Settings = Depends(get_settings)):
    """Every known connector with its access kind (official API / licensed / scraping) and state."""
    result = []
    for info in CATALOGUE.values():
        enabled = is_enabled(info.name, settings.connectors_enabled, settings.connectors_allow_scraping)
        result.append(
            ConnectorRead(
                name=info.name,
                label=info.label,
                access=info.access,
                enabled=enabled,
                implemented=info.implemented,
                usable=enabled and info.implemented,
                note=info.note,
            )
        )
    return result
