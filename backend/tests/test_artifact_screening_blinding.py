"""A non-owner sees a screening-decision artifact only when they made the decision themselves (X.265)."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.artifacts import SCREENING_DECISION_KIND, register_artifact
from app.database import SessionLocal
from app.models import Artifact, ArtifactStatus, ProjectStage, utcnow
from tests.api_helpers import sign_in_user


def _world():
    owner, _ = sign_in_user("blind-art-owner@example.com")
    project_id = owner.post("/api/projects", json={"title": "Blinded"}).json()["id"]
    mine, mine_id = sign_in_user("blind-art-mine@example.com")
    _, other_id = sign_in_user("blind-art-other@example.com")
    for email in ("blind-art-mine@example.com", "blind-art-other@example.com"):
        owner.post(f"/api/projects/{project_id}/members", json={"email": email, "role": "reviewer"})
    with SessionLocal() as db:
        for actor, ref in ((mine_id, "d-mine"), (other_id, "d-other")):
            register_artifact(db, uuid.UUID(project_id), SCREENING_DECISION_KIND, ref, ProjectStage.screened, actor=actor)
        register_artifact(db, uuid.UUID(project_id), "search", "s1", ProjectStage.retrieved, actor=other_id)
        for row in db.scalars(select(Artifact).where(Artifact.project_id == uuid.UUID(project_id))):
            row.status, row.stale_at = ArtifactStatus.stale, utcnow()
        db.commit()
    return owner, mine, project_id


def test_a_reviewer_lists_only_their_own_screening_artifacts_and_everyones_other_artifacts():
    owner, mine, project_id = _world()

    seen = {(a["kind"], a["ref_id"]) for a in mine.get(f"/api/projects/{project_id}/artifacts").json()}
    assert (SCREENING_DECISION_KIND, "d-mine") in seen
    assert (SCREENING_DECISION_KIND, "d-other") not in seen
    assert ("search", "s1") in seen

    all_refs = {(a["kind"], a["ref_id"]) for a in owner.get(f"/api/projects/{project_id}/artifacts").json()}
    assert {(SCREENING_DECISION_KIND, "d-mine"), (SCREENING_DECISION_KIND, "d-other")} <= all_refs


def test_the_rerun_path_hides_another_reviewers_screening_artifacts_from_a_non_owner():
    owner, mine, project_id = _world()

    def refs(client):
        steps = client.get(f"/api/projects/{project_id}/rerun-path").json()
        return {a["ref_id"] for step in steps for a in step["artifacts"]}

    assert "d-other" not in refs(mine)
    assert {"d-mine", "s1"} <= refs(mine)
    assert {"d-mine", "d-other", "s1"} <= refs(owner)
