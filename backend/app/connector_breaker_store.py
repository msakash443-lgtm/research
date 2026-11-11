"""Wires the DB-backed circuit breaker (M1.3.8) onto connectors from `connectors/factory.py`,
when `CONNECTOR_BREAKER_SHARED` is on. Lives outside `app/connectors/`: that package may not
import the DB (`test_connector_interface.py`'s static import-forbidding test enforces it),
and connectors never write the DB themselves (M1.3.1).

Off by default: without the setting, `build_connector` here behaves exactly like
`connectors.factory.build_connector` — each call gets a fresh, purely in-memory breaker
(M1.3.2), the same as before this module existed.
"""

from __future__ import annotations

from app.config import get_settings
from app.connectors.base import Connector
from app.connectors.factory import build_connector as _build_connector
from app.database import SessionLocal
from app.models import ConnectorBreakerState


class DbBreakerSync:
    """Reads/writes one connector's breaker state in its own short transaction, independent of
    any caller's, so a remembered failure survives even if the caller's own work later rolls
    back, and a connector call never holds a DB transaction open for an external HTTP request."""

    def __init__(self, name: str):
        self.name = name

    def load(self) -> tuple[int, float | None]:
        with SessionLocal() as db:
            row = db.get(ConnectorBreakerState, self.name)
            if row is None:
                return 0, None
            return row.failures, row.open_until

    def save(self, failures: int, open_until: float | None) -> None:
        with SessionLocal() as db:
            row = db.get(ConnectorBreakerState, self.name)
            if row is None:
                db.add(ConnectorBreakerState(name=self.name, failures=failures, open_until=open_until))
            else:
                row.failures = failures
                row.open_until = open_until
            db.commit()


def build_connector(name: str) -> Connector:
    """Same as `connectors.factory.build_connector`, plus a shared breaker when enabled."""
    connector = _build_connector(name)
    if get_settings().connector_breaker_shared:
        http_client = getattr(connector, "_http", None)
        if http_client is not None:
            http_client.attach_breaker_sync(DbBreakerSync(connector.name))
    return connector
