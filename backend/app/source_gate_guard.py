"""Prevent late source inserts from changing the corpus after human G4 approval."""

from __future__ import annotations

from sqlalchemy import event, select

from app.models import Gate, GateCode, GateStatus, Project, Source


class ExtractionGateLocked(Exception):
    """Re-entry is required before a new source can join an approved corpus."""


@event.listens_for(Source, "before_insert")
def _guard_source_insert(mapper, connection, source: Source) -> None:
    # G4 approval locks Project then Gate in this order; use the same locks here.
    connection.execute(select(Project.id).where(Project.id == source.project_id).with_for_update()).scalar_one_or_none()
    gate_status = connection.execute(
        select(Gate.status).where(Gate.project_id == source.project_id, Gate.code == GateCode.G4).with_for_update()
    ).scalar_one_or_none()
    if gate_status == GateStatus.approved:
        raise ExtractionGateLocked("Gate G4 is approved; reopen the extraction stage before adding a source")
