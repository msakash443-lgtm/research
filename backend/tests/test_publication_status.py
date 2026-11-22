"""Publication status (X.196): a preprint is labelled, and with peer_reviewed_only on it cannot be cited."""

import json
import uuid

from tests.test_citation_guard import (  # noqa: F401  (fixtures + helpers of the real citation pipeline)
    add_source, assert_blocked, automatic_check, rejections, research, services, world,
)


def flag_as_preprint(sid):
    from app.database import SessionLocal
    from app.models import Source
    with SessionLocal() as db:
        db.get(Source, uuid.UUID(sid)).quality_flags = ["preprint"]
        db.commit()


def set_peer_reviewed_only(c, pid, enabled):
    return c.put(f"/api/projects/{pid}/screening/peer-reviewed-only", json={"enabled": enabled})


def test_with_peer_reviewed_only_on_a_run_citing_a_preprint_is_rejected(world, services, fake_llm):
    c, pid = world
    sid = add_source(c, pid)
    assert automatic_check(c, pid, sid)["metadata_verified"] is True
    flag_as_preprint(sid)
    assert set_peer_reviewed_only(c, pid, True).json() == {"enabled": True}

    run = research(c, pid)

    assert_blocked(run, rejections(c, pid), 1, "preprint_citation", "cited a preprint ([S1])")
    assert run["input_snapshot"]["sources"][0]["preprint_blocked"] is True
    audits = [e["action"] for e in c.get(f"/api/projects/{pid}/audit").json()]
    assert "screening.peer_reviewed_only_changed" in audits


def test_with_peer_reviewed_only_off_a_preprint_is_cited_with_its_preprint_label(world, services, fake_llm):
    c, pid = world
    sid = add_source(c, pid)
    assert automatic_check(c, pid, sid)["metadata_verified"] is True
    flag_as_preprint(sid)

    run = research(c, pid)

    assert run["status"] == "completed" and run["answer"] == "Fake answer [S1]."
    user = json.loads(fake_llm.requests[0].content)["messages"][1]["content"]
    assert "preprint, not peer reviewed" in user


def test_only_a_manager_may_change_peer_reviewed_only(world):
    c, pid = world
    assert c.get(f"/api/projects/{pid}/screening/peer-reviewed-only").json() == {"enabled": False}
    from fastapi.testclient import TestClient
    from app.main import app
    stranger = TestClient(app)
    stranger.post("/api/auth/development/login", json={"email": f"pr-{uuid.uuid4().hex[:8]}@example.com", "display_name": "S"})
    assert set_peer_reviewed_only(stranger, pid, True).status_code == 404


def test_the_screening_queue_flags_preprints_and_counts_them(world):
    c, pid = world
    sid = add_source(c, pid)
    flag_as_preprint(sid)
    set_peer_reviewed_only(c, pid, True)

    queue = c.get(f"/api/projects/{pid}/screening/queue").json()

    assert queue["peer_reviewed_only"] is True and queue["preprint_pending"] == 1
    assert [item["preprint"] for item in queue["items"]] == [True]


def test_the_evidence_matrix_row_of_a_preprint_carries_the_preprint_label(world):
    c, pid = world
    sid = add_source(c, pid)
    flag_as_preprint(sid)

    papers = c.get(f"/api/projects/{pid}/evidence-matrix").json()["papers"]

    assert [(p["publication_status"], p["peer_reviewed_only_excluded"]) for p in papers] == [("preprint, not peer reviewed", False)]
