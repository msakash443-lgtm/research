import math
import types
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select

import app.task_handlers as handlers
from app.agent import embeddings as embeddings_module
from app.agent.embeddings import OpenAICompatibleEmbeddings, parse_embeddings
from app.agent.llm import LLMConfigurationError, LLMResponseError
from app.config import get_settings
from app.database import SessionLocal
from app.main import app
from app.models import Artifact, AuditEvent, ClusterRun, Project, ProjectStage, ScreeningDecision, Source, SourceEmbedding
from app.task_registry import ClaimedTask, PermanentTaskError
from app.thematic_clusters import ClusteringError, _fill_empty, clusterable_sources, kmeans, run_clustering

# Two clear themes: titles mentioning "sleep" point one way, "remote work" the other.
TOPICS = {"sleep": [1.0, 0.1, 0.0], "remote": [0.0, 0.2, 1.0]}


class FakeEmbedder:
    model = "test-embedding"

    def __init__(self, fail_with=None):
        self.calls = []
        self.fail_with = fail_with

    def embed(self, texts):
        self.calls.append(list(texts))
        if self.fail_with:
            raise self.fail_with
        out = []
        for text in texts:
            base = TOPICS["sleep"] if "sleep" in text.lower() else TOPICS["remote"]
            wobble = (len(text) % 7) / 100  # deterministic, small
            out.append([base[0] + wobble, base[1], base[2] + wobble / 2])
        return out


@pytest.fixture
def world():
    tag = uuid.uuid4().hex[:8]
    owner = TestClient(app)
    owner.post("/api/auth/development/login", json={"email": f"clu-{tag}@example.com", "display_name": "C"})
    pid = owner.post("/api/projects", json={"title": "Clusters"}).json()["id"]
    titles = ["Sleep and memory", "Sleep loss in nurses", "Sleep hygiene apps", "Remote work and burnout", "Remote work productivity"]
    ids = [owner.post(f"/api/projects/{pid}/sources", json={"title": t}).json()["id"] for t in titles]
    return {"owner": owner, "pid": pid, "ids": ids, "titles": titles}


def cluster(world, embedder=None, k=2, seed=0, **kwargs):
    embedder = embedder or FakeEmbedder()
    with SessionLocal() as db:
        project = db.get(Project, uuid.UUID(world["pid"]))
        run = run_clustering(db, project, embedder=embedder, k=k, seed=seed, actor="tester", batch_size=kwargs.pop("batch_size", 64),
                             max_sources=kwargs.pop("max_sources", 1000), **kwargs)
        db.commit()
        return run.id, embedder


def groups(world, run_id):
    data = world["owner"].get(f"/api/projects/{world['pid']}/clusters/{run_id}").json()
    return [{m["title"] for m in c["members"]} for c in data["clusters"]], data


# ---- k-means ------------------------------------------------------------------------------------

def test_kmeans_separates_two_obvious_groups_and_is_repeatable():
    vectors = [[1, 0, 0], [0.9, 0.1, 0], [0, 0, 1], [0.05, 0, 0.95], [0.95, 0.05, 0]]
    first = kmeans(vectors, 2, seed=7)
    assert first == kmeans(vectors, 2, seed=7)
    assignment, distances = first
    assert assignment[0] == assignment[1] == assignment[4] != assignment[2] == assignment[3]
    assert all(0 <= d < 0.05 for d in distances)


def test_kmeans_returns_exactly_k_non_empty_clusters():
    vectors = [[1, 0], [0.99, 0.01], [0.98, 0.02], [0, 1]]
    assignment, _ = kmeans(vectors, 3, seed=1)
    assert sorted(set(assignment)) == [0, 1, 2]


def test_an_empty_cluster_takes_the_worst_fitting_point_of_a_cluster_that_can_spare_one():
    points = [[1, 0], [0.8, 0.6], [0, 1]]
    centres = [[1, 0], [0, 1], [0.6, 0.8]]
    # cluster 2 is empty; point 1 fits its centre (cluster 0) worst among points whose cluster has two members
    assert _fill_empty([0, 0, 1], points, centres, 3) == [0, 2, 1]
    assert _fill_empty([0, 1, 1], points, centres, 2) == [0, 1, 1]  # nothing empty: unchanged


def test_kmeans_refuses_impossible_requests():
    with pytest.raises(ClusteringError, match="between 2"):
        kmeans([[1, 0], [0, 1]], 3, seed=0)
    with pytest.raises(ClusteringError, match="distinct"):
        kmeans([[1, 0], [2, 0], [3, 0]], 2, seed=0)  # all the same direction
    with pytest.raises(ClusteringError, match="all zeros"):
        kmeans([[0, 0], [1, 0]], 2, seed=0)


# ---- embedding replies --------------------------------------------------------------------------

def test_parse_embeddings_orders_by_index():
    data = {"data": [{"index": 1, "embedding": [0, 1]}, {"index": 0, "embedding": [1, 0]}]}
    assert parse_embeddings(data, 2) == [[1.0, 0.0], [0.0, 1.0]]


@pytest.mark.parametrize("data, message", [
    ({"data": [{"index": 0, "embedding": [1, 0]}]}, "1 vectors for 2"),
    ({"error": "nope"}, "no vectors"),
    ({"data": [{"index": 0, "embedding": [1, 0]}, {"index": 0, "embedding": [0, 1]}]}, "repeated"),
    ({"data": [{"index": 0, "embedding": [1, 0]}, {"index": 1, "embedding": [math.nan, 1]}]}, "finite"),
    ({"data": [{"index": 0, "embedding": [1, 0]}, {"index": 1, "embedding": ["1", 0]}]}, "finite"),
    ({"data": [{"index": 0, "embedding": [1, 0]}, {"index": 1, "embedding": [1, 0, 0]}]}, "different lengths"),
    ({"data": [{"index": 0, "embedding": [1, 0]}, {"index": 1, "embedding": [0, 0]}]}, "all-zero"),
])
def test_parse_embeddings_rejects_bad_replies(data, message):
    with pytest.raises(LLMResponseError, match=message):
        parse_embeddings(data, 2)


def test_the_client_posts_to_embeddings_and_needs_a_model(monkeypatch):
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.5, 0.5]}], "usage": {"prompt_tokens": 3, "total_tokens": 3}})

    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(embeddings_module, "httpx", types.SimpleNamespace(
        Client=lambda **kw: httpx.Client(transport=transport, **kw), HTTPError=httpx.HTTPError))
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_api_base_url", "http://llm.test/v1")
    monkeypatch.setattr(settings, "llm_api_key", SecretStr("k"))
    with pytest.raises(LLMConfigurationError, match="EMBEDDING_MODEL"):
        OpenAICompatibleEmbeddings(settings).embed(["x"])
    monkeypatch.setattr(settings, "embedding_model", "emb-1")
    assert OpenAICompatibleEmbeddings(settings).embed(["x"]) == [[0.5, 0.5]]
    assert str(seen[0].url) == "http://llm.test/v1/embeddings"
    assert seen[0].headers["Authorization"] == "Bearer k"


# ---- running a clustering -----------------------------------------------------------------------

def test_a_run_groups_the_themes_with_neutral_labels_and_is_recorded(world):
    run_id, _ = cluster(world)
    found, data = groups(world, run_id)
    assert sorted(found, key=len) == [{"Remote work and burnout", "Remote work productivity"}, {"Sleep and memory", "Sleep loss in nurses", "Sleep hygiene apps"}]
    assert [c["label"] for c in data["clusters"]] == ["Cluster 1", "Cluster 2"]  # largest first; nobody named them yet
    assert (data["model_id"], data["k"], data["seed"], data["n_sources"], data["status"]) == ("test-embedding", 2, 0, 5, "current")
    with SessionLocal() as db:
        artifact = db.scalar(select(Artifact).where(Artifact.ref_id == str(run_id)))
        assert (artifact.kind, artifact.stage.value, artifact.created_by) == ("thematic_clusters", "synthesized", "agent:clustering")
        event = db.scalar(select(AuditEvent).where(AuditEvent.action == "clusters.created"))
        assert event.model_id == "test-embedding" and event.payload_json["n_sources"] == 5


def test_the_same_seed_gives_the_same_clusters(world):
    first, _ = cluster(world, seed=3)
    second, _ = cluster(world, seed=3)
    assert groups(world, first)[0] == groups(world, second)[0]


def test_embeddings_are_reused_and_only_changed_text_is_embedded_again(world):
    _, embedder = cluster(world)
    assert sum(len(c) for c in embedder.calls) == 5
    _, again = cluster(world)
    assert again.calls == []  # nothing changed
    with SessionLocal() as db:
        db.get(Source, uuid.UUID(world["ids"][0])).abstract = "We study sleep and recall."
        db.commit()
    _, after_edit = cluster(world)
    assert len(after_edit.calls) == 1 and len(after_edit.calls[0]) == 1 and "sleep and recall" in after_edit.calls[0][0]


def test_embedding_happens_in_batches(world):
    _, embedder = cluster(world, batch_size=2)
    assert [len(c) for c in embedder.calls] == [2, 2, 1]


def test_sources_a_person_excluded_are_left_out(world):
    excluded = world["ids"][3]
    world["owner"].put(f"/api/projects/{world['pid']}/criteria", json={"framework": "custom", "criteria": [
        {"kind": "include", "text": "Empirical"}, {"kind": "exclude", "text": "Opinion"}]})
    response = world["owner"].post(f"/api/projects/{world['pid']}/screening/decisions",
                                   json={"source_id": excluded, "decision": "exclude", "reason_code": "E1"})
    assert response.status_code == 201, response.text
    with SessionLocal() as db:
        db.add(ScreeningDecision(project_id=uuid.UUID(world["pid"]), source_id=uuid.UUID(world["ids"][4]), stage="title_abstract", seq=1,
                                 decision="exclude", reason_code="E1", decided_by="ai", decider="agent:prescreen", confidence=0.9))
        db.commit()  # an AI suggestion to exclude is not a decision: that source stays in
        names = {s.title for s in clusterable_sources(db, db.get(Project, uuid.UUID(world["pid"])))}
    assert "Remote work and burnout" not in names and "Remote work productivity" in names
    run_id, _ = cluster(world)
    assert groups(world, run_id)[1]["n_sources"] == 4


def test_an_embedding_failure_stores_nothing(world):
    with pytest.raises(LLMResponseError):
        cluster(world, embedder=FakeEmbedder(fail_with=LLMResponseError("down")))
    with SessionLocal() as db:
        assert db.scalars(select(ClusterRun)).all() == [] and db.scalars(select(SourceEmbedding)).all() == []


def test_too_few_or_too_many_sources_is_refused(world):
    with pytest.raises(ClusteringError, match="at least 6"):
        cluster(world, k=6)
    with pytest.raises(ClusteringError, match="limited to 4"):
        cluster(world, max_sources=4)


# ---- API ----------------------------------------------------------------------------------------

def test_asking_while_clustering_is_off_is_refused_and_queues_nothing(world):
    response = world["owner"].post(f"/api/projects/{world['pid']}/clusters", json={"k": 2})
    assert response.status_code == 409 and "EMBEDDING_MODEL" in response.json()["detail"]
    with SessionLocal() as db:
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "clusters.requested")) is None


def test_asking_queues_a_task_with_the_run_id(world, monkeypatch):
    monkeypatch.setattr(get_settings(), "embedding_model", "test-embedding")
    response = world["owner"].post(f"/api/projects/{world['pid']}/clusters", json={"k": 2, "seed": 5})
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "queued"
    assert world["owner"].post(f"/api/projects/{world['pid']}/clusters", json={"k": 6}).status_code == 422  # only 5 sources
    assert world["owner"].post(f"/api/projects/{world['pid']}/clusters", json={"k": 1}).status_code == 422
    assert world["owner"].post(f"/api/projects/{world['pid']}/clusters", json={"k": 2, "extra": 1}).status_code == 422

    monkeypatch.setattr("app.task_queue.owns_task", lambda *a, **k: True)
    monkeypatch.setattr(handlers, "OpenAICompatibleEmbeddings", lambda settings, **kw: FakeEmbedder())
    task = ClaimedTask(id=body["task_id"], project_id=uuid.UUID(world["pid"]), type="thematic_clustering",
                       payload={"project_id": world["pid"], "run_id": body["run_id"], "k": 2, "seed": 5, "actor": "tester"}, attempt=1)
    handlers.handle_thematic_clustering(task)
    handlers.handle_thematic_clustering(task)  # a retry after the run was stored adds nothing
    listed = world["owner"].get(f"/api/projects/{world['pid']}/clusters").json()
    assert [r["id"] for r in listed] == [body["run_id"]] and listed[0]["seed"] == 5


def test_the_handler_fails_permanently_when_not_configured(world, monkeypatch):
    monkeypatch.setattr("app.task_queue.owns_task", lambda *a, **k: True)
    task = ClaimedTask(id=uuid.uuid4(), project_id=uuid.UUID(world["pid"]), type="thematic_clustering",
                       payload={"project_id": world["pid"], "run_id": str(uuid.uuid4()), "k": 2, "seed": 0}, attempt=1)
    with pytest.raises(PermanentTaskError, match="EMBEDDING_MODEL"):
        handlers.handle_thematic_clustering(task)


def test_a_person_renames_a_cluster_and_the_audit_keeps_ids_only(world):
    run_id, _ = cluster(world)
    data = groups(world, run_id)[1]
    target = data["clusters"][0]["id"]
    url = f"/api/projects/{world['pid']}/clusters/{run_id}/clusters/{target}"
    assert world["owner"].patch(url, json={"label": "   "}).status_code == 422
    response = world["owner"].patch(url, json={"label": "  Sleep   and cognition "})
    assert response.status_code == 200
    assert response.json()["label"] == "Sleep and cognition" and response.json()["label_edited_by"]
    assert groups(world, run_id)[1]["clusters"][0]["label"] == "Sleep and cognition"
    with SessionLocal() as db:
        event = db.scalar(select(AuditEvent).where(AuditEvent.action == "clusters.label_changed"))
        assert event.payload_json == {"run_id": str(run_id), "cluster_id": target}
    assert world["owner"].patch(f"/api/projects/{world['pid']}/clusters/{uuid.uuid4()}/clusters/{target}", json={"label": "x"}).status_code == 404


def test_another_projects_clusters_are_not_reachable(world):
    run_id, _ = cluster(world)
    other = world["owner"].post("/api/projects", json={"title": "Other"}).json()["id"]
    assert world["owner"].get(f"/api/projects/{other}/clusters/{run_id}").status_code == 404
    assert world["owner"].get(f"/api/projects/{other}/clusters").json() == []


def test_going_back_to_an_earlier_stage_marks_the_clusters_stale(world):
    run_id, _ = cluster(world)
    with SessionLocal() as db:
        db.get(Project, uuid.UUID(world["pid"])).stage = ProjectStage.synthesized
        db.commit()
    response = world["owner"].post(f"/api/projects/{world['pid']}/stage/reenter", json={"stage": "scoped", "reason": "Scope changed"})
    assert response.status_code == 200, response.text
    data = groups(world, run_id)[1]
    assert data["status"] == "stale" and data["stale_reason"]
