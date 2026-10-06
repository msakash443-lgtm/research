import json
import re
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import task_handlers, task_runner
from app.config import get_settings
from app.connectors.base import ConnectorBase, ConnectorError, PaperRecord, SearchPage
from app.database import SessionLocal
from app.main import app
from app.models import Source

TITLE = "Remote work and worker productivity: evidence from a field experiment"
AUTHORS = ["Ada Lovelace", "Grace Hopper"]
DOI = "10.1234/rw.2020"


def rec(title=TITLE, authors=tuple(AUTHORS), year=2020, doi=DOI, flags=()):
    return PaperRecord(connector="crossref", external_id="c1", title=title, authors=authors, year=year, doi=doi, quality_flags=flags)


class FakeService(ConnectorBase):
    def __init__(self, name, record=None, error=None):
        self.name, self.record, self.error = name, record, error

    def get_by_id(self, external_id):
        if self.error:
            raise ConnectorError(self.error)
        return self.record

    def search(self, request):
        if self.error:
            raise ConnectorError(self.error)
        return SearchPage(records=(self.record,) if self.record else ())


@pytest.fixture
def services(monkeypatch):
    """Control what the verifier's services answer: services.record / services.error."""
    state = type("S", (), {"record": rec(), "error": None})()
    monkeypatch.setattr(get_settings(), "connectors_enabled", ["crossref"])
    monkeypatch.setattr(task_handlers, "build_connector", lambda name: FakeService(name, state.record, state.error))
    return state


@pytest.fixture
def world():
    tag = uuid.uuid4().hex[:8]
    c = TestClient(app)
    c.post("/api/auth/development/login", json={"email": f"sv-{tag}@example.com", "display_name": "V"})
    pid = c.post("/api/projects", json={"title": "Verify"}).json()["id"]
    return c, pid


def add(c, pid, **over):
    body = {"title": TITLE, "authors": AUTHORS, "year": 2020, "doi": DOI, **over}
    r = c.post(f"/api/projects/{pid}/sources", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def check(c, pid, sid):
    r = c.post(f"/api/projects/{pid}/sources/{sid}/check")
    assert r.status_code == 202, r.text
    while task_runner.run_one_task():
        pass


def get(c, pid, sid):
    return next(s for s in c.get(f"/api/projects/{pid}/sources").json() if s["id"] == sid)


def audit_of(c, pid, action):
    return [e for e in c.get(f"/api/projects/{pid}/audit").json() if e["action"] == action]


def test_a_matching_record_verifies_the_source_automatically_and_records_how(world, services):
    c, pid = world
    sid = add(c, pid)
    assert get(c, pid, sid)["metadata_verified"] is False

    check(c, pid, sid)

    s = get(c, pid, sid)
    assert s["metadata_verified"] is True and s["verification_method"] == "automatic" and s["verified_at"]
    assert s["verification"]["verdict"] == "verified" and s["verification"]["matched"] == "crossref:c1"
    (event,) = audit_of(c, pid, "source.verified")
    assert event["actor"] == "agent:citation-verifier" and event["payload_json"]["method"] == "automatic"
    assert event["payload_json"]["requested_by"]  # the person who asked is on the record
    assert audit_of(c, pid, "source.verification_checked")[0]["payload_json"]["verdict"] == "verified"


def test_other_verdicts_store_the_check_but_never_verify(world, services):
    c, pid = world
    for record, error, verdict in (
        (rec(year=2018, authors=("Jane Roe",)), None, "mismatch"),
        (None, None, "not_found"),
        (None, "HTTP 503", "unavailable"),
    ):
        services.record, services.error = record, error
        sid = add(c, pid, doi=f"10.1234/x{verdict}")
        check(c, pid, sid)
        s = get(c, pid, sid)
        assert s["metadata_verified"] is False and s["verification_method"] is None
        assert s["verification"]["verdict"] == verdict and s["verified_at"] is None
    assert audit_of(c, pid, "source.verified") == []


def test_an_incomplete_source_is_recorded_as_incomplete(world, services):
    c, pid = world
    sid = c.post(f"/api/projects/{pid}/sources", json={"title": "Only a title here"}).json()["id"]
    check(c, pid, sid)
    s = get(c, pid, sid)
    assert s["verification"]["verdict"] == "incomplete" and s["metadata_verified"] is False


def _retrieved(pid, **kw):
    with SessionLocal() as db:
        src = Source(project_id=uuid.UUID(pid), title=TITLE, authors=AUTHORS, year=2020, doi=DOI, source_type="openalex", origin="retrieved", **kw)
        db.add(src)
        db.commit()
        return str(src.id)


def test_an_automatic_check_is_not_a_persons_review(world, services):
    c, pid = world
    sid = _retrieved(pid)
    check(c, pid, sid)
    s = get(c, pid, sid)
    assert s["metadata_verified"] is True and s["verification_method"] == "automatic" and s["is_automated"] is True
    with SessionLocal() as db:
        assert db.scalar(select(Source.is_automated).where(Source.id == uuid.UUID(sid))) is True  # SQL agrees
        assert db.get(Source, uuid.UUID(sid)).reviewed_by_person is False

    confirmed = c.post(f"/api/projects/{pid}/sources/{sid}/verify").json()
    assert confirmed["verification_method"] == "human" and confirmed["is_automated"] is False
    human = [e for e in audit_of(c, pid, "source.verified") if e["payload_json"]["method"] == "human"]
    assert len(human) == 1 and human[0]["payload_json"]["confirmed_automatic_check"] is True


def test_the_prompt_says_an_automatic_check_is_not_a_review(world, services, fake_llm):
    c, pid = world
    sid = _retrieved(pid)
    with SessionLocal() as db:
        from app.models import SourceExcerpt, excerpt_hash

        db.add(SourceExcerpt(source_id=uuid.UUID(sid), content="An excerpt", content_hash=excerpt_hash("An excerpt")))
        db.commit()
    check(c, pid, sid)
    r = c.post(f"/api/projects/{pid}/research-runs", json={"question": "What does the evidence say?"})
    assert r.status_code == 202
    user = json.loads(fake_llm.requests[0].content)["messages"][1]["content"]
    assert "automatically retrieved; metadata matched by an automatic check, not reviewed by a person" in user
    assert "added by a researcher" not in user


def test_a_later_mismatch_revokes_an_automatic_verification_but_not_a_persons(world, services):
    c, pid = world
    auto = add(c, pid)
    check(c, pid, auto)
    assert get(c, pid, auto)["metadata_verified"] is True
    services.record = rec(year=2001)
    check(c, pid, auto)
    s = get(c, pid, auto)
    assert s["metadata_verified"] is False and s["verification_method"] is None and s["verification"]["verdict"] == "mismatch"
    assert len(audit_of(c, pid, "source.verification_revoked")) == 1

    human = add(c, pid, doi="10.1234/human")
    c.post(f"/api/projects/{pid}/sources/{human}/verify")
    check(c, pid, human)  # services now disagree
    h = get(c, pid, human)
    assert h["metadata_verified"] is True and h["verification_method"] == "human" and h["verification"]["verdict"] == "mismatch"


def test_not_found_or_outage_does_not_revoke(world, services):
    c, pid = world
    sid = add(c, pid)
    check(c, pid, sid)
    services.record = None
    check(c, pid, sid)
    services.error = "HTTP 503"
    check(c, pid, sid)
    assert get(c, pid, sid)["metadata_verified"] is True and audit_of(c, pid, "source.verification_revoked") == []


def test_a_retraction_is_added_to_the_flags(world, services):
    c, pid = world
    services.record = rec(flags=("retracted",))
    sid = add(c, pid)
    check(c, pid, sid)
    assert "retracted" in get(c, pid, sid)["quality_flags"]


def test_merging_clears_a_stale_automatic_verification(world, services):
    c, pid = world
    a = add(c, pid)
    check(c, pid, a)
    b = add(c, pid)
    with SessionLocal() as db:
        db.get(Source, uuid.UUID(b)).abstract = "A brand new abstract from an unchecked duplicate."
        db.commit()
    merged = c.post(f"/api/projects/{pid}/sources/merge", json={"source_ids": [a, b], "keep": a}).json()
    assert merged["metadata_verified"] is False and merged["verification_method"] is None and merged["verification"] is None


def test_requests_are_validated(world, services, monkeypatch):
    c, pid = world
    sid = add(c, pid)
    assert c.post(f"/api/projects/{pid}/sources/{uuid.uuid4()}/check").status_code == 404
    assert c.post(f"/api/projects/{pid}/sources/{sid}/check").status_code == 202
    assert c.post(f"/api/projects/{pid}/sources/{sid}/check").status_code == 409  # one pending check per source
    monkeypatch.setattr(get_settings(), "connectors_enabled", [])
    other = add(c, pid, doi="10.1234/other")
    assert c.post(f"/api/projects/{pid}/sources/{other}/check").status_code == 400


def test_only_the_person_route_and_the_verifier_can_set_metadata_verified():
    app_dir = Path(__file__).resolve().parents[1] / "app"
    pattern = re.compile(r"metadata_verified\s*=\s*True")
    setters = sorted(p.name for p in app_dir.rglob("*.py") if pattern.search(p.read_text(encoding="utf-8")))
    assert setters == ["source_verification.py", "sources.py"]
    handler = (app_dir / "task_handlers.py").read_text(encoding="utf-8")
    assert "verify_source(" in handler and pattern.search(handler) is None
