import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models import Source, SourceExcerpt


@pytest.fixture
def world():
    tag = uuid.uuid4().hex[:8]
    c = TestClient(app)
    c.post("/api/auth/development/login", json={"email": f"merge-{tag}@example.com", "display_name": "M"})
    pid = c.post("/api/projects", json={"title": "Merge"}).json()["id"]
    return c, pid


def add(c, pid, **body):
    r = c.post(f"/api/projects/{pid}/sources", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def merge(c, pid, ids, **extra):
    return c.post(f"/api/projects/{pid}/sources/merge", json={"source_ids": ids, **extra})


def listing(c, pid):
    return {s["id"]: s for s in c.get(f"/api/projects/{pid}/sources").json()}


def set_fields(sid, **fields):
    with SessionLocal() as db:
        src = db.get(Source, uuid.UUID(sid))
        for k, v in fields.items():
            setattr(src, k, v)
        db.commit()


def merged_event(c, pid):
    return next(e for e in c.get(f"/api/projects/{pid}/audit").json() if e["action"] == "source.merged")


def test_merge_fills_the_kept_source_and_hides_the_others_without_deleting(world):
    c, pid = world
    a = add(c, pid, title="Remote work and output", doi="10.1234/rw", year=2020)
    b = add(c, pid, title="Remote work and output", doi="10.1234/RW")
    set_fields(b, abstract="A long abstract.", venue="Journal X", source_ids={"openalex": "W1"}, oa_url="https://oa.example/1")

    r = merge(c, pid, [a, b], keep=a)

    assert r.status_code == 200 and r.json()["id"] == a
    body = r.json()
    assert body["abstract"] == "A long abstract." and body["venue"] == "Journal X" and body["year"] == 2020
    assert body["source_ids"] == {"openalex": "W1"}  # no internal bookkeeping keys leak in
    assert list(listing(c, pid)) == [a]
    with SessionLocal() as db:
        hidden = db.get(Source, uuid.UUID(b))
        assert hidden is not None and hidden.merged_into == uuid.UUID(a)


def test_excerpts_move_to_the_kept_source_unchanged(world):
    c, pid = world
    a = add(c, pid, title="Same work title here", doi="10.1234/x", evidence_excerpt="first quote")
    b = add(c, pid, title="Same work title here", doi="10.1234/x", evidence_excerpt="second quote")
    with SessionLocal() as db:
        before = {
            e.content: e.content_hash
            for e in db.scalars(select(SourceExcerpt))
            if e.source_id in (uuid.UUID(a), uuid.UUID(b))
        }
    assert len(before) == 2

    assert merge(c, pid, [a, b], keep=a).status_code == 200

    with SessionLocal() as db:
        rows = db.scalars(select(SourceExcerpt).where(SourceExcerpt.source_id == uuid.UUID(a))).all()
        assert {e.content: e.content_hash for e in rows} == before


def test_different_dois_and_unrelated_works_are_refused_and_change_nothing(world):
    c, pid = world
    a = add(c, pid, title="Remote work and output", doi="10.1234/one")
    b = add(c, pid, title="Remote work and output", doi="10.1234/two")
    d = add(c, pid, title="Completely unrelated study of bees")
    before = listing(c, pid)

    for ids in ([a, b], [a, d]):
        assert merge(c, pid, ids).status_code == 409
    assert listing(c, pid) == before
    events = {e["action"] for e in c.get(f"/api/projects/{pid}/audit").json()}
    assert "source.merged" not in events


def test_bad_requests(world):
    c, pid = world
    a = add(c, pid, title="Remote work and output", doi="10.1234/one")
    b = add(c, pid, title="Remote work and output", doi="10.1234/one")
    assert merge(c, pid, [a]).status_code == 422
    assert merge(c, pid, [a, a]).status_code == 409  # one distinct source
    assert merge(c, pid, [a, str(uuid.uuid4())]).status_code == 409
    assert merge(c, pid, [a, b], keep=str(uuid.uuid4())).status_code == 409
    assert merge(c, pid, [a, b], keep=a).status_code == 200
    assert merge(c, pid, [a, b], keep=a).status_code == 409  # b is already merged
    assert c.post(f"/api/projects/{pid}/sources/{b}/verify").status_code == 404  # merged source is gone from the API


def test_a_source_from_another_project_cannot_be_merged(world):
    c, pid = world
    other = c.post("/api/projects", json={"title": "Other"}).json()["id"]
    a = add(c, pid, title="Remote work and output", doi="10.1234/one")
    foreign = add(c, other, title="Remote work and output", doi="10.1234/one")
    assert merge(c, pid, [a, foreign]).status_code == 409
    assert foreign in listing(c, other)


def test_default_kept_source_is_the_verified_one_then_the_oldest_and_keep_overrides(world):
    c, pid = world
    old = add(c, pid, title="Remote work and output", doi="10.1234/one")
    newer = add(c, pid, title="Remote work and output", doi="10.1234/one")
    assert c.post(f"/api/projects/{pid}/sources/{newer}/verify").status_code == 200
    assert merge(c, pid, [old, newer]).json()["id"] == newer

    p2 = c.post("/api/projects", json={"title": "Second"}).json()["id"]
    x = add(c, p2, title="Remote work and output", doi="10.1234/one")
    y = add(c, p2, title="Remote work and output", doi="10.1234/one")
    assert merge(c, p2, [x, y], keep=y).json()["id"] == y


def test_verification_survives_only_when_nothing_unverified_was_taken_in(world):
    c, pid = world
    a = add(c, pid, title="Remote work and output", doi="10.1234/one", year=2020)
    b = add(c, pid, title="Remote work and output", doi="10.1234/one", year=2020)
    set_fields(b, source_ids={"pmid": "9"})  # only an id is added: no field value comes from b
    c.post(f"/api/projects/{pid}/sources/{a}/verify")
    assert merge(c, pid, [a, b], keep=a).json()["metadata_verified"] is True

    p2 = c.post("/api/projects", json={"title": "Second"}).json()["id"]
    x = add(c, p2, title="Remote work and output", doi="10.1234/one", year=2020)
    y = add(c, p2, title="Remote work and output", doi="10.1234/one", year=2020)
    set_fields(y, abstract="An unverified abstract that is longer.")
    c.post(f"/api/projects/{p2}/sources/{x}/verify")
    r = merge(c, p2, [x, y], keep=x)
    assert r.json()["abstract"] == "An unverified abstract that is longer."
    assert r.json()["metadata_verified"] is False  # nobody confirmed this merged metadata
    assert merged_event(c, p2)["payload_json"]["verification_cleared"] is True


def test_an_unverified_kept_source_is_never_promoted_by_a_verified_duplicate(world):
    c, pid = world
    a = add(c, pid, title="Remote work and output", doi="10.1234/one")
    b = add(c, pid, title="Remote work and output", doi="10.1234/one")
    c.post(f"/api/projects/{pid}/sources/{b}/verify")
    assert merge(c, pid, [a, b], keep=a).json()["metadata_verified"] is False


def test_audit_event_has_ids_and_decisions_but_no_text(world):
    c, pid = world
    a = add(c, pid, title="Distinctive title about telework", doi="10.1234/one")
    b = add(c, pid, title="Distinctive title about telework", doi="10.1234/one")
    set_fields(b, abstract="Secret abstract words")
    merge(c, pid, [a, b], keep=a)
    event = merged_event(c, pid)
    assert event["actor"] and event["payload_json"]["kept"] == a and event["payload_json"]["merged"] == [b]
    assert "Secret abstract" not in json.dumps(event) and "telework" not in json.dumps(event)


def test_merged_sources_are_left_out_of_the_prompt(world, fake_llm):
    c, pid = world
    a = add(c, pid, title="Remote work and output", doi="10.1234/one", evidence_excerpt="quote one")
    b = add(c, pid, title="Remote work and output", doi="10.1234/one", evidence_excerpt="quote two")
    merge(c, pid, [a, b])
    r = c.post(f"/api/projects/{pid}/research-runs", json={"question": "What does the evidence say?"})
    assert r.status_code == 202
    user = json.loads(fake_llm.requests[0].content)["messages"][1]["content"]
    assert user.count("<<<SOURCE [S") == 1


def test_without_a_verified_source_the_oldest_is_kept(world):
    from datetime import datetime, timezone

    c, pid = world
    a = add(c, pid, title="Remote work and output", doi="10.1234/one")
    b = add(c, pid, title="Remote work and output", doi="10.1234/one")
    set_fields(a, created_at=datetime(2026, 1, 2, tzinfo=timezone.utc))
    set_fields(b, created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert merge(c, pid, [a, b]).json()["id"] == b
