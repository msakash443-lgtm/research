"""Citation guard (plan M1.10.3/M1.10.4): a generated answer may only cite verified sources.

Each reference goes through the real pipeline: saved as a source, checked by the citation
verifier (services faked), then cited by the (faked) model in a research run.
"""

import json
import re
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import task_handlers, task_runner
from app.agent import executor
from app.config import get_settings
from app.connectors.base import ConnectorBase, PaperRecord, SearchPage
from app.main import app
from app.prompt_registry import load_prompt
from tests.llm_replies import chat_reply

TITLE = "Remote work and worker productivity: evidence from a field experiment"
AUTHORS = ["Ada Lovelace", "Grace Hopper"]
DOI = "10.1234/rw.2020"


def record(title=TITLE, authors=tuple(AUTHORS), year=2020, doi=DOI):
    return PaperRecord(connector="crossref", external_id="c1", title=title, authors=authors, year=year, doi=doi)


class FakeService(ConnectorBase):
    def __init__(self, name, found):
        self.name, self.found = name, found

    def get_by_id(self, external_id):
        return self.found

    def search(self, request):
        return SearchPage(records=(self.found,) if self.found else ())


@pytest.fixture
def services(monkeypatch):
    """What the bibliographic services answer when a source is checked: set `services.found`."""
    state = type("S", (), {"found": record()})()
    monkeypatch.setattr(get_settings(), "connectors_enabled", ["crossref"])
    monkeypatch.setattr(task_handlers, "build_connector", lambda name: FakeService(name, state.found))
    return state


@pytest.fixture
def world():
    c = TestClient(app)
    c.post("/api/auth/development/login", json={"email": f"cg-{uuid.uuid4().hex[:8]}@example.com", "display_name": "C"})
    return c, c.post("/api/projects", json={"title": "Citation guard"}).json()["id"]


def add_source(c, pid, **over):
    body = {
        "title": TITLE, "authors": AUTHORS, "year": 2020, "doi": DOI,
        "evidence_excerpt": "Productivity rose 13% among home workers.", "locator": "p. 4", **over,
    }
    response = c.post(f"/api/projects/{pid}/sources", json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def automatic_check(c, pid, sid):
    assert c.post(f"/api/projects/{pid}/sources/{sid}/check").status_code == 202
    while task_runner.run_one_task():
        pass
    return next(s for s in c.get(f"/api/projects/{pid}/sources").json() if s["id"] == sid)


def research(c, pid):
    response = c.post(f"/api/projects/{pid}/research-runs", json={"question": "Does remote work raise productivity?"})
    assert response.status_code == 202, response.text
    return response.json()


def rejections(c, pid):
    return [e["payload_json"] for e in c.get(f"/api/projects/{pid}/audit").json() if e["action"] == "research_run.citation_rejected"]


def assert_blocked(run, rejected, index, reason, message):
    assert run["status"] == "failed"
    assert run["answer"] is None  # nothing stored, no placeholder
    assert message in run["error_message"]
    assert rejected == [{"run_id": run["id"], "reason": reason, "cited_index": index}]


def test_a_fabricated_reference_is_blocked_from_the_answer(world, services, fake_llm):
    c, pid = world
    services.found = None  # no service knows this paper
    sid = add_source(c, pid, title="Quantum yoga improves regression coefficients", doi="10.9999/fake.2026.001")
    assert automatic_check(c, pid, sid)["metadata_verified"] is False

    run = research(c, pid)

    assert_blocked(run, rejections(c, pid), 1, "unverified_citation", "cited an unverified source ([S1])")


def test_a_reference_with_mismatched_metadata_is_blocked(world, services, fake_llm):
    c, pid = world
    services.found = record(year=2017, authors=("Someone Else",))  # real DOI, invented details
    sid = add_source(c, pid)
    checked = automatic_check(c, pid, sid)
    assert checked["metadata_verified"] is False and checked["verification"]["verdict"] == "mismatch"

    assert_blocked(research(c, pid), rejections(c, pid), 1, "unverified_citation", "cited an unverified source ([S1])")


def test_a_known_good_reference_can_be_cited(world, services, fake_llm):
    c, pid = world
    sid = add_source(c, pid)
    assert automatic_check(c, pid, sid)["metadata_verified"] is True

    run = research(c, pid)

    assert run["status"] == "completed"
    assert run["answer"] == "Fake answer [S1]."
    assert rejections(c, pid) == []


def test_a_source_verified_by_a_person_can_be_cited(world, fake_llm):
    c, pid = world
    sid = add_source(c, pid)
    assert c.post(f"/api/projects/{pid}/sources/{sid}/verify").status_code == 200

    assert research(c, pid)["status"] == "completed"


def test_citing_a_source_the_run_does_not_have_is_blocked(world, fake_llm):
    c, pid = world
    sid = add_source(c, pid)
    assert c.post(f"/api/projects/{pid}/sources/{sid}/verify").status_code == 200
    fake_llm.handler = lambda request: chat_reply("Productivity rose [S1][S2].")

    assert_blocked(research(c, pid), rejections(c, pid), 2, "unknown_citation_index", "cited [S2], which doesn't match any source")


@pytest.mark.parametrize("citation", ["[S1, S2]", "[S1; S2]", "[S1 and S2]", "[S1-S2]", "[S1–S2]", "[s1, s2]"])
def test_every_source_in_a_grouped_citation_is_checked(world, fake_llm, citation):
    c, pid = world
    verified = add_source(c, pid, title="A verified study", doi="10.1234/one")
    add_source(c, pid, title="An unverified study", doi="10.1234/two")
    assert c.post(f"/api/projects/{pid}/sources/{verified}/verify").status_code == 200
    fake_llm.handler = lambda request: chat_reply(f"Productivity rose {citation}.")

    run = research(c, pid)

    unverified_index = next(i for i, s in enumerate(run["input_snapshot"]["sources"], start=1) if not s["verified"])
    assert_blocked(run, rejections(c, pid), unverified_index, "unverified_citation", "cited an unverified source")


BLOCK = re.compile(r"<<<SOURCE \[S(\d+)\] id=[0-9a-f]+>>>\n(.*?)\n<<<END", re.DOTALL)


def test_the_prompt_tells_the_model_which_sources_it_may_cite(world, fake_llm):
    """M1.10.5: the model is told up front which sources it may cite, not only blocked afterwards."""
    c, pid = world
    verified = add_source(c, pid, title="A verified study", doi="10.1234/one")
    add_source(c, pid, title="An unverified study", doi="10.1234/two")
    assert c.post(f"/api/projects/{pid}/sources/{verified}/verify").status_code == 200

    run = research(c, pid)

    system, user = (m["content"] for m in json.loads(fake_llm.requests[0].content)["messages"])
    assert 'cite only a source whose Status line says "Verified — may be cited."' in system
    blocks = {}
    for _, block in BLOCK.findall(user):
        title_line = block.split("\n", 1)[0]
        blocks[title_line.removeprefix("Title: ")] = block
    assert "Verified — may be cited." in blocks["A verified study"]
    assert "NOT VERIFIED — do not cite this source." in blocks["An unverified study"]
    assert "Verified — may be cited." not in blocks["An unverified study"]
    assert run["prompt_version"] == "evidence_synthesis@4"


def test_older_prompt_versions_render_without_the_citation_marker():
    """v2 runs stay reproducible: their source blocks never gain the v3 marker."""
    source = {"title": "T", "excerpt": "E", "automated": False, "verified": True}

    _, user = executor._agent_prompts(load_prompt("evidence_synthesis", 2), SimpleNamespace(question="Q?"), [], [source])

    assert "Status: added by a researcher\n" in user and "may be cited" not in user
