"""Test setup for gates. Gates are decided in order (M0.5.10), so a test about one gate first needs the
earlier ones approved. This writes them directly, with no audit events, so tests that check an audit
trail still see only the decisions they make themselves."""

import uuid

from app.database import SessionLocal
from app.gates import GATE_ORDER, ensure_gates
from app.models import GateCode, GateStatus, Project, utcnow


def approve_earlier_gates(project_id, code: GateCode | str, decided_by: str = "test-setup") -> None:
    """Approve every gate before `code` that isn't approved yet (a real earlier approval is left alone)."""
    code = GateCode(code)
    earlier = set(GATE_ORDER[: GATE_ORDER.index(code)])
    with SessionLocal() as db:
        project = db.get(Project, uuid.UUID(str(project_id)))
        for gate in ensure_gates(db, project):
            if gate.code in earlier and gate.status != GateStatus.approved:  # keep real decisions
                gate.status, gate.decided_by, gate.decided_at = GateStatus.approved, decided_by, utcnow()
        db.commit()
