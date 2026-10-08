"""Plan M1.9: snowballing (backward/forward, N rounds, caps) and its records entering screening with provenance."""

import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import task_handlers, task_runner
from app.connectors.base import ConnectorBase, ConnectorError, PaperRecord
from app.connectors.http import ConnectorHttpClient, HttpPolicy
from app.connectors.openalex import OpenAlexConnector
from app.config import get_settings
from app.database import SessionLocal
from app.main import app
from app.models import Gate, GateCode, GateStatus, SnowballRun, Source
from app.snowball import KnownWorks, SnowballError, Start, walk
from gate_helpers import approve_earlier_gates


def rec(n, with_doi=True, **over):
    fields = {"connector": "openalex", "external_id": f"W{n}", "title": f"A distinct and long enough title number {n}",
              "year": 2000 + n, "authors": (f"Author{n} Family{n}",), "source_ids": {"openalex": f"W{n}"}}
    if with_doi:
        fields["doi"] = f"10.1000/p{n}"
    fields.update(over)
    return PaperRecord(**fields)


# W1 cites W2, W3; W2 cites W4; W5 cites W1; W6 cites W2 (so W6 is forward from W2).
REFS = {"W1": ["W2", "W3"], "W2": ["W4"], "W3": [], "W4": [], "W5": ["W1"], "W6": ["W2"]}


class Graph(ConnectorBase):
    name = "openalex"
    fail_on: str | None = None
    calls: list = []

    def __init__(self):
        self.max_related = 200
        self.last_related_truncated = False
        self.skipped_records = 0

    def _n(self, wid):
        return int(wid[1:])

    def get_by_doi(self, doi):
        Graph.calls.append(("doi", doi))
        n = int(doi.rsplit("p", 1)[1])
        return rec(n) if f"W{n}" in REFS else None

    def get_references(self, wid):
        Graph.calls.append(("refs", wid))
        if Graph.fail_on == wid:
            raise ConnectorError("upstream down")
        ids = REFS[wid]
        self.last_related_truncated = len(ids) > self.max_related
        return tuple(rec(self._n(i)) for i in ids[: self.max_related])

    def get_citations(self, wid):
        Graph.calls.append(("cites", wid))
        if Graph.fail_on == wid:
            raise ConnectorError("upstream down")
        ids = [k for k, v in REFS.items() if wid in v]
        self.last_related_truncated = len(ids) > self.max_related
        return tuple(rec(self._n(i)) for i in ids[: self.max_related])


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(get_settings(), "connectors_enabled", ["openalex"])
    monkeypatch.setattr(task_handlers, "build_connector", lambda name: Graph())
    Graph.fail_on = None
    Graph.calls = []


# --- the walk itself (no database) -------------------------------------------------


def test_backward_one_round_records_where_each_work_came_from():
    result = walk(Graph(), [Start(via={"title": "W1"}, external_id="W1")], directions=("backward",), rounds=1, max_new=50, known=KnownWorks("openalex"))
    assert [(f.record.external_id, f.direction, f.round, f.via["title"]) for f in result.found] == [("W2", "backward", 1, "W1"), ("W3", "backward", 1, "W1")]
    assert result.rounds == [{"round": 1, "papers_expanded": 1, "fetched": 2, "new": 2, "already_in_project": 0, "repeated": 0, "truncated_papers": 0}]


def test_two_rounds_both_directions_follow_what_round_one_found_and_skip_repeats():
    result = walk(Graph(), [Start(via={"title": "W1"}, external_id="W1")], directions=("backward", "forward"), rounds=2, max_new=50, known=KnownWorks("openalex"))
    found = {f.record.external_id: (f.direction, f.round, f.via["title"]) for f in result.found}
    assert found["W2"] == ("backward", 1, "W1") and found["W5"] == ("forward", 1, "W1")
    assert found["W4"][1] == 2 and found["W6"] == ("forward", 2, "A distinct and long enough title number 2")
    assert "W1" not in found  # the start paper is never "found" again
    assert result.rounds[1]["repeated"] >= 1  # W5's reference back to W1, W2's citation by W1


def test_works_already_in_the_project_are_counted_not_added():
    known = KnownWorks("openalex")
    known.add(rec(2))  # same DOI
    known.add(rec(3, with_doi=False, external_id="X9", source_ids={}))  # same title+year+first author, no DOI
    result = walk(Graph(), [Start(via={"title": "W1"}, external_id="W1")], directions=("backward",), rounds=1, max_new=50, known=known)
    assert result.found == [] and result.rounds[0]["already_in_project"] == 2


def test_a_different_doi_is_never_taken_for_the_same_work():
    known = KnownWorks("openalex")
    known.add(rec(2, with_doi=False, external_id="X9", source_ids={}, title="A distinct and long enough title number 2"))
    known.add(rec(3, external_id="X8", source_ids={}, doi="10.9999/other"))
    result = walk(Graph(), [Start(via={"title": "W1"}, external_id="W1")], directions=("backward",), rounds=1, max_new=50, known=known)
    # W2 has a DOI, the known copy has none but the same work key: a duplicate. W3's DOI differs: kept.
    assert [f.record.external_id for f in result.found] == ["W3"]


def test_the_new_record_cap_stops_the_run_and_says_so():
    result = walk(Graph(), [Start(via={"title": "W1"}, external_id="W1")], directions=("backward", "forward"), rounds=3, max_new=2, known=KnownWorks("openalex"))
    assert len(result.found) == 2 and result.capped is True
    assert len(result.rounds) == 1


def test_per_paper_truncation_is_counted():
    graph = Graph()
    graph.max_related = 1
    result = walk(graph, [Start(via={"title": "W1"}, external_id="W1")], directions=("backward",), rounds=1, max_new=50, known=KnownWorks("openalex"))
    assert len(result.found) == 1 and result.rounds[0]["truncated_papers"] == 1


def test_start_papers_are_resolved_by_doi_and_unresolvable_ones_are_listed():
    starts = [Start(via={"title": "seed"}, doi="10.1000/p1"), Start(via={"title": "no doi"}), Start(via={"title": "unknown"}, doi="10.1000/p99")]
    result = walk(Graph(), starts, directions=("backward",), rounds=1, max_new=50, known=KnownWorks("openalex"))
    assert [s["resolved"] for s in result.starts] == [True, False, False]
    assert result.starts[0]["external_id"] == "W1"
    assert "no DOI" in result.starts[1]["reason"] and "no record" in result.starts[2]["reason"]
    assert len(result.found) == 2


def test_no_resolvable_start_paper_fails_loudly():
    with pytest.raises(SnowballError):
        walk(Graph(), [Start(via={"title": "x"})], directions=("backward",), rounds=1, max_new=5, known=KnownWorks("openalex"))


def test_openalex_looks_up_a_start_paper_by_doi():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path.endswith("missing"):
            return httpx.Response(404, json={})
        return httpx.Response(200, json={"id": "https://openalex.org/W7", "display_name": "Found by DOI", "doi": "https://doi.org/10.1000/abc"})

    http = ConnectorHttpClient("openalex", "https://api.openalex.org", HttpPolicy(max_retries=0), transport=httpx.MockTransport(handler))
    oa = OpenAlexConnector(http)
    assert oa.get_by_doi("https://doi.org/10.1000/ABC").external_id == "W7"
    assert seen[-1] == "/works/doi:10.1000/abc"
    assert oa.get_by_doi("10.1000/missing") is None
    with pytest.raises(ConnectorError):
        oa.get_by_doi("not a doi")


# --- through the API and the task queue -------------------------------------------


def _login(email):
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": email, "display_name": "S"})
    return client


@pytest.fixture
def project():
    tag = uuid.uuid4().hex[:8]
    owner = _login(f"snow-{tag}@example.com")
    pid = owner.post("/api/projects", json={"title": "Snowball"}).json()["id"]
    owner.post(f"/api/projects/{pid}/seeds", json={"title": "The seed paper we start from", "doi": "10.1000/p1"})
    approve_earlier_gates(pid, "G2")
    return owner, pid


def run_all():
    while task_runner.run_one_task():
        pass


def approve_g2(owner, pid):
    assert owner.post(f"/api/projects/{pid}/gates/G2/approve").status_code == 200


def sources(pid):
    with SessionLocal() as db:
        return db.scalars(select(Source).where(Source.project_id == uuid.UUID(pid)).order_by(Source.created_at)).all()


def actions(owner, pid):
    return [e["action"] for e in owner.get(f"/api/projects/{pid}/audit").json()]


def test_a_snowball_waits_for_g2_then_adds_unverified_sources_with_provenance(project):
    owner, pid = project
    queued = owner.post(f"/api/projects/{pid}/snowball", json={"connector": "openalex", "directions": ["backward", "forward"]})
    assert queued.status_code == 202 and queued.json()["status"] == "blocked" and queued.json()["blocked_by_gate"] == "G2"
    run_all()
    assert Graph.calls == [] and sources(pid) == []
    assert owner.get(f"/api/projects/{pid}/snowball").json()["pending"][0]["status"] == "blocked"

    approve_g2(owner, pid)
    run_all()
    added = sources(pid)
    assert sorted(s.source_ids["openalex"] for s in added) == ["W2", "W3", "W5"]
    for s in added:
        assert s.origin == "retrieved" and s.metadata_verified is False and s.created_by == "agent:snowball"
        assert s.found_via["method"] == "snowball" and s.found_via["run_id"] == queued.json()["run_id"]
        assert s.found_via["via"]["title"] == "The seed paper we start from" and "seed_id" in s.found_via["via"]
    assert {s.found_via["direction"] for s in added} == {"backward", "forward"}

    listing = owner.get(f"/api/projects/{pid}/snowball").json()
    assert listing["pending"] == []
    (run,) = listing["runs"]
    assert run["id"] == queued.json()["run_id"] and run["counts"]["added"] == 3 and run["counts"]["capped"] is False
    assert run["starts"][0]["resolved"] is True

    queue = owner.get(f"/api/projects/{pid}/screening/queue").json()
    assert len(queue["items"]) == 3 and all(i["found_via"]["method"] == "snowball" for i in queue["items"])
    assert owner.get(f"/api/projects/{pid}/sources").json()[0]["found_via"]["method"] == "snowball"

    prisma = owner.get(f"/api/projects/{pid}/prisma").json()
    assert prisma["other_methods"]["citation_searching"] == {"runs": 1, "identified": 3, "added": 3, "already_in_project": 0}
    assert "snowball.requested" in actions(owner, pid) and "snowball.run" in actions(owner, pid)


def test_a_second_run_does_not_re_add_and_a_retried_task_runs_once(project):
    owner, pid = project
    approve_g2(owner, pid)
    owner.post(f"/api/projects/{pid}/snowball", json={"connector": "openalex", "directions": ["backward"]})
    run_all()
    first = len(sources(pid))
    calls = len(Graph.calls)
    run_all()
    assert len(Graph.calls) == calls  # the finished task is not run again

    owner.post(f"/api/projects/{pid}/snowball", json={"connector": "openalex", "directions": ["backward"]})
    run_all()
    assert len(sources(pid)) == first
    runs = owner.get(f"/api/projects/{pid}/snowball").json()["runs"]
    assert [r["counts"]["added"] for r in runs] == [2, 0] and runs[1]["counts"]["already_in_project"] == 2


def test_a_handler_rerun_after_saving_is_a_no_op(project, monkeypatch):
    owner, pid = project
    approve_g2(owner, pid)
    run_id = owner.post(f"/api/projects/{pid}/snowball", json={"connector": "openalex", "directions": ["backward"]}).json()["run_id"]
    run_all()
    with SessionLocal() as db:
        assert db.get(SnowballRun, uuid.UUID(run_id)) is not None

    class Claimed:
        payload = {"project_id": pid, "run_id": run_id, "connector": "openalex", "directions": ["backward"], "rounds": 1,
                   "max_per_paper": 50, "max_new": 200, "include_seeds": True, "source_ids": []}

        def ensure_owned(self, db=None):
            pass

    before = len(sources(pid))
    task_handlers.handle_snowball_run(Claimed())
    assert len(sources(pid)) == before


def test_a_service_failure_saves_nothing_and_is_audited(project):
    owner, pid = project
    approve_g2(owner, pid)
    Graph.fail_on = "W1"
    owner.post(f"/api/projects/{pid}/snowball", json={"connector": "openalex", "directions": ["backward"]})
    task_runner.run_one_task()
    assert sources(pid) == []
    assert owner.get(f"/api/projects/{pid}/snowball").json()["runs"] == []
    assert "snowball.failed" in actions(owner, pid)


def test_chosen_sources_can_be_start_papers(project):
    owner, pid = project
    approve_g2(owner, pid)
    sid = owner.post(f"/api/projects/{pid}/sources", json={"title": "A core paper I included", "doi": "10.1000/p2"}).json()["id"]
    owner.post(f"/api/projects/{pid}/snowball", json={"connector": "openalex", "directions": ["backward"], "include_seeds": False, "source_ids": [sid]})
    run_all()
    new = [s for s in sources(pid) if s.found_via]
    assert [s.source_ids["openalex"] for s in new] == ["W4"] and new[0].found_via["via"]["source_id"] == sid


def test_bad_requests_are_refused(project):
    owner, pid = project
    url = f"/api/projects/{pid}/snowball"
    assert owner.post(url, json={"connector": "crossref"}).status_code == 422
    assert owner.post(url, json={"connector": "semantic_scholar"}).status_code == 400  # not enabled here
    assert owner.post(url, json={"connector": "openalex", "rounds": 4}).status_code == 422
    assert owner.post(url, json={"connector": "openalex", "include_seeds": False}).status_code == 422
    assert owner.post(url, json={"connector": "openalex", "source_ids": [str(uuid.uuid4())]}).status_code == 404


def test_no_new_records_once_screening_is_approved(project):
    owner, pid = project
    with SessionLocal() as db:
        gate = db.scalar(select(Gate).where(Gate.project_id == uuid.UUID(pid), Gate.code == GateCode.G3))
        if gate is None:
            gate = Gate(project_id=uuid.UUID(pid), code=GateCode.G3)
            db.add(gate)
        gate.status = GateStatus.approved
        db.commit()
    assert owner.post(f"/api/projects/{pid}/snowball", json={"connector": "openalex"}).status_code == 409
