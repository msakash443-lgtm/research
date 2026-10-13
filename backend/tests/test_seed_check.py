"""Plan M2.9.1: seed-based false-exclusion check (spec 11 "false-exclusion rate < 2%")."""

import json
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.task_handlers as handlers
from app import seed_check as sc
from app.agent.llm import OpenAICompatibleLLM
from app.config import get_settings
from app.database import SessionLocal
from app.main import app
from app.models import AuditEvent, Project, ScreeningDecision, SeedCheckRun
from app.prescreen import PrescreenError
from app.seed_check_runner import run_seed_check
from app.task_registry import REQUIRED_GATES, ClaimedTask, PermanentTaskError
from app.models import GateCode

LONG = "Remote work and the wellbeing of employees in knowledge organisations"


def paper(**kw):
    return SimpleNamespace(**{"title": LONG, "doi": None, "year": None, "authors": None, **kw})


# ---- matching and counting (pure) ---------------------------------------------------------------------

def test_a_seed_matches_the_same_doi_or_the_same_title_year_and_author_but_never_a_different_doi():
    sources = [paper(doi="10.1234/aaa", title="Other"), paper(doi="10.1234/bbb", title="Sleep and memory consolidation in adults", year=2020, authors=["Lovelace, Ada"])]
    seeds = [
        paper(doi="https://doi.org/10.1234/AAA", title="x"),
        paper(title="Sleep and memory consolidation in adults", year=2020, authors=["Ada Lovelace"]),
        paper(doi="10.1234/zzz", title="Sleep and memory consolidation in adults", year=2020, authors=["Ada Lovelace"]),  # other DOI
        paper(title="Sleep and memory consolidation in adults", year=2019, authors=["Ada Lovelace"]),  # other year
        paper(title="Short"),
    ]
    got = [(m.status, m.matched_by) for m in sc.match_seeds(seeds, sources)]
    assert got == [(None, "doi"), (None, "title_year_author"), (sc.NOT_IN_SOURCES, None), (sc.NOT_IN_SOURCES, None), (sc.CANNOT_CHECK, None)]


def _answered(n, excluded=0, maybe=0):
    kinds = ["exclude"] * excluded + ["maybe"] * maybe + ["include"] * (n - excluded - maybe)
    return [{"status": sc.ANSWERED, "decision": d, "auto_exclude_eligible": d == "exclude"} for d in kinds]


@pytest.mark.parametrize("n, excluded, status", [(50, 0, sc.BELOW_LIMIT), (50, 1, sc.AT_OR_ABOVE_LIMIT), (51, 1, sc.BELOW_LIMIT), (20, 1, sc.AT_OR_ABOVE_LIMIT)])
def test_the_limit_is_strict_and_compared_on_the_exact_fraction(n, excluded, status):
    summary = sc.summarise(_answered(n, excluded))
    assert summary["status"] == status and summary["excluded"] == excluded


def test_a_maybe_is_not_a_false_exclusion_and_a_small_sample_is_flagged():
    summary = sc.summarise(_answered(10, excluded=0, maybe=3))
    assert summary["excluded"] == 0 and summary["maybe"] == 3 and summary["false_exclusion_rate"] == 0.0
    assert any("fewer than 50" in note for note in summary["notes"]) and summary["rough"] is True


def test_a_failed_seed_makes_the_whole_result_incomplete_even_with_no_exclusions():
    summary = sc.summarise(_answered(60) + [{"status": sc.FAILED}])
    assert summary["status"] == sc.INCOMPLETE and summary["failed"] == 1
    # "no rate is judged" must mean no rate is reported either: a figure over only the seeds that answered would read as a result
    assert summary["false_exclusion_rate"] is None


def test_no_seeds_and_nothing_matched_are_stated_not_scored():
    assert sc.summarise([])["status"] == sc.NO_SEEDS
    unmatched = sc.summarise([{"status": sc.NOT_IN_SOURCES}, {"status": sc.CANNOT_CHECK}])
    assert unmatched["status"] == sc.NOTHING_TO_CHECK and unmatched["false_exclusion_rate"] is None


# ---- the run -------------------------------------------------------------------------------------------

class KeyedLLM(OpenAICompatibleLLM):
    """The real adapter with the HTTP call replaced: the reply is chosen by a marker in the record's title."""

    def __init__(self, replies):
        super().__init__(get_settings(), api_key=None, api_base_url="http://llm.invalid", model="test-model", require_api_key=False)
        self.replies, self.asked = replies, []

    def complete(self, system_prompt, user_prompt):
        marker = next(m for m in self.replies if m in user_prompt)
        self.asked.append(marker)
        reply = self.replies[marker]
        return reply if isinstance(reply, str) else json.dumps(reply)


def answer(decision="include", reason=None, confidence=0.9):
    return {"decision": decision, "reason_code": reason, "confidence": confidence, "rationale": "Because."}


@pytest.fixture
def world():
    tag = uuid.uuid4().hex[:8]
    owner = TestClient(app)
    owner.post("/api/auth/development/login", json={"email": f"seed-{tag}@example.com", "display_name": "S"})
    pid = owner.post("/api/projects", json={"title": "Seed check"}).json()["id"]
    owner.put(f"/api/projects/{pid}/criteria", json={"framework": "custom", "criteria": [
        {"kind": "include", "text": "Empirical studies"}, {"kind": "exclude", "text": "Opinion pieces"}]})
    for marker, doi in (("ALPHA", "10.1234/aaa"), ("BRAVO", "10.1234/bbb")):
        owner.post(f"/api/projects/{pid}/sources", json={"title": f"{marker} {LONG}", "doi": doi})
    owner.post(f"/api/projects/{pid}/sources", json={"title": f"CHARLIE {LONG}", "year": 2020, "authors": ["Lovelace, Ada"]})
    owner.post(f"/api/projects/{pid}/sources", json={"title": f"DELTA {LONG}", "doi": "10.1234/ddd"})  # not a seed
    for body in (
        {"title": "A seed", "doi": "10.1234/aaa"},
        {"title": "B seed", "doi": "10.1234/bbb"},
        {"title": f"CHARLIE {LONG}", "year": 2020, "authors": ["Ada Lovelace"]},
        {"title": "Never retrieved", "doi": "10.1234/zzz"},
    ):
        assert owner.post(f"/api/projects/{pid}/seeds", json=body).status_code in (200, 201)
    return {"owner": owner, "pid": pid}


def check(world, replies, **kwargs):
    llm = KeyedLLM(replies)
    with SessionLocal() as db:
        project = db.get(Project, uuid.UUID(world["pid"]))
        run = run_seed_check(
            db, project, llm=llm, min_confidence=kwargs.pop("min_confidence", 0.6), autoexclude_default_threshold=0.9,
            actor="tester", run_id=uuid.uuid4(), **kwargs,
        )
        db.expunge(run)
    return run, llm


def test_every_matched_seed_is_asked_and_the_unmatched_one_is_reported_without_a_model_call(world):
    run, llm = check(world, {"ALPHA": answer("include", "I1"), "BRAVO": answer("exclude", "E1", 0.95), "CHARLIE": answer("include", "I1")})
    assert sorted(llm.asked) == ["ALPHA", "BRAVO", "CHARLIE"]  # DELTA is not a seed; the unmatched seed costs nothing
    s = run.summary
    assert (s["seeds"], s["checked"], s["excluded"], s["not_in_sources"], s["failed"]) == (4, 3, 1, 1, 0)
    assert s["false_exclusion_rate"] == 0.333 and s["status"] == sc.AT_OR_ABOVE_LIMIT and s["auto_exclude_eligible"] == 1
    excluded = next(e for e in run.entries if e.get("decision") == "exclude")
    assert excluded["title"] == "B seed" and excluded["reason_code"] == "E1" and excluded["rationale"] == "Because."


def test_a_source_set_to_never_for_this_item_is_not_sent_to_the_model_and_is_reported_as_locked(world):
    sources = world["owner"].get(f"/api/projects/{world['pid']}/sources").json()
    alpha = next(s for s in sources if s["title"].startswith("ALPHA"))
    assert world["owner"].put(f"/api/projects/{world['pid']}/ai-locks/source/{alpha['id']}").status_code == 200

    run, llm = check(world, {"BRAVO": answer(), "CHARLIE": answer()})

    assert sorted(llm.asked) == ["BRAVO", "CHARLIE"]
    locked = next(e for e in run.entries if e["status"] == sc.AI_LOCKED)
    assert locked["title"] == "A seed" and "decision" not in locked
    assert run.summary["ai_locked"] == 1 and run.summary["checked"] == 2
    assert any("never for this item" in note for note in run.summary["notes"])


def test_the_run_records_the_model_prompt_and_settings_and_audits_it_with_ids_and_counts_only(world):
    run, _ = check(world, {"ALPHA": answer(), "BRAVO": answer(), "CHARLIE": answer()})
    assert (run.model_id, run.prompt_version, run.min_confidence, run.autoexclude_threshold) == ("test-model", "screening_prescreen@1", 0.6, 0.9)
    with SessionLocal() as db:
        event = db.scalar(select(AuditEvent).where(AuditEvent.action == "screening.seed_check_run", AuditEvent.project_id == uuid.UUID(world["pid"])))
    assert event.model_id == "test-model" and event.prompt_version == "screening_prescreen@1"
    assert event.payload_json == {"run_id": str(run.id), "status": "below_limit", "checked": 3, "excluded": 0, "failed": 0}


def test_nothing_reaches_screening_decisions_so_the_queue_and_prisma_are_untouched(world):
    check(world, {"ALPHA": answer("exclude", "E1"), "BRAVO": answer(), "CHARLIE": answer()})
    with SessionLocal() as db:
        assert db.scalars(select(ScreeningDecision).where(ScreeningDecision.project_id == uuid.UUID(world["pid"]))).all() == []
    assert world["owner"].get(f"/api/projects/{world['pid']}/screening/queue").json()["counts"]["unscreened"] == 4


def test_a_hesitant_exclude_is_a_maybe_exactly_as_in_pre_screening(world):
    run, _ = check(world, {"ALPHA": answer("exclude", "E1", 0.3), "BRAVO": answer(), "CHARLIE": answer()})
    assert run.summary["excluded"] == 0 and run.summary["maybe"] == 1


def test_an_unusable_answer_is_kept_on_its_seed_and_the_run_is_incomplete_not_guessed(world):
    run, _ = check(world, {"ALPHA": answer("exclude", "I1"), "BRAVO": answer(), "CHARLIE": answer()})  # exclude with an include code
    assert run.summary["status"] == sc.INCOMPLETE and run.summary["failed"] == 1 and run.summary["rough"]
    failed = next(e for e in run.entries if e["status"] == sc.FAILED)
    assert failed["title"] == "A seed" and "rejected" in failed["reason"] and "decision" not in failed


def test_it_refuses_without_both_kinds_of_criteria(world):
    world["owner"].put(f"/api/projects/{world['pid']}/criteria", json={"framework": "custom", "criteria": [{"kind": "include", "text": "Empirical"}]})
    with pytest.raises(PrescreenError, match="exclusion criterion"):
        check(world, {})


def test_a_stored_run_cannot_be_changed_or_deleted(world):
    run, _ = check(world, {"ALPHA": answer(), "BRAVO": answer(), "CHARLIE": answer()})
    with SessionLocal() as db:
        row = db.get(SeedCheckRun, run.id)
        row.entries = []
        with pytest.raises(ValueError, match="append-only"):
            db.commit()
        db.rollback()
        db.delete(db.get(SeedCheckRun, run.id))
        with pytest.raises(ValueError, match="append-only"):
            db.commit()


# ---- the task and the API ------------------------------------------------------------------------------

def _claimed(world, run_id, monkeypatch):
    monkeypatch.setattr("app.task_queue.owns_task", lambda *a, **k: True)
    payload = {"project_id": world["pid"], "run_id": str(run_id), "actor": "tester"}
    return ClaimedTask(id=uuid.uuid4(), project_id=uuid.UUID(world["pid"]), type=handlers.SEED_FALSE_EXCLUSION_CHECK, payload=payload, attempt=1)


def test_the_task_type_waits_for_gate_g2():
    assert REQUIRED_GATES[handlers.SEED_FALSE_EXCLUSION_CHECK] == GateCode.G2


def test_the_handler_stores_the_run_and_a_retry_does_not_ask_the_model_again(world, monkeypatch):
    llm = KeyedLLM({"ALPHA": answer(), "BRAVO": answer(), "CHARLIE": answer()})
    monkeypatch.setattr(handlers, "llm_for_project", lambda settings, project, **kw: llm)
    run_id = uuid.uuid4()
    handlers.handle_seed_false_exclusion_check(_claimed(world, run_id, monkeypatch))
    assert len(llm.asked) == 3
    handlers.handle_seed_false_exclusion_check(_claimed(world, run_id, monkeypatch))
    assert len(llm.asked) == 3
    with SessionLocal() as db:
        assert len(db.scalars(select(SeedCheckRun)).all()) == 1


def test_the_handler_stores_an_incomplete_run_and_then_fails_the_task(world, monkeypatch):
    monkeypatch.setattr(handlers, "llm_for_project", lambda settings, project, **kw: KeyedLLM({"ALPHA": "bad", "BRAVO": answer(), "CHARLIE": answer()}))
    run_id = uuid.uuid4()
    with pytest.raises(PermanentTaskError, match="1 seed paper"):
        handlers.handle_seed_false_exclusion_check(_claimed(world, run_id, monkeypatch))
    with SessionLocal() as db:
        assert db.get(SeedCheckRun, run_id).summary["status"] == sc.INCOMPLETE


def test_the_handler_fails_loudly_with_no_model_configured_and_stores_nothing(world, monkeypatch):
    class Unconfigured(OpenAICompatibleLLM):
        def __init__(self, settings, **kw):
            super().__init__(settings, api_key=None, api_base_url=None, model=None)

    monkeypatch.setattr(handlers, "llm_for_project", lambda settings, project, **kw: Unconfigured(settings))
    with pytest.raises(PermanentTaskError, match="LLM"):
        handlers.handle_seed_false_exclusion_check(_claimed(world, uuid.uuid4(), monkeypatch))
    with SessionLocal() as db:
        assert db.scalars(select(SeedCheckRun)).all() == []


def _url(world):
    return f"/api/projects/{world['pid']}/screening/seed-check"


def test_asking_queues_one_task_that_waits_for_g2_and_asking_again_returns_it(world):
    first = world["owner"].post(_url(world))
    assert first.status_code == 202 and first.json()["status"] == "blocked" and first.json()["blocked_by_gate"] == "G2"
    again = world["owner"].post(_url(world))
    assert again.json()["task_id"] == first.json()["task_id"] and again.json()["run_id"] == first.json()["run_id"]
    got = world["owner"].get(_url(world)).json()
    assert got["latest"] is None and got["pending"]["run_id"] == first.json()["run_id"]


def test_the_latest_stored_run_is_returned_with_its_counts_and_provenance(world, monkeypatch):
    monkeypatch.setattr(handlers, "llm_for_project", lambda settings, project, **kw: KeyedLLM({"ALPHA": answer(), "BRAVO": answer("exclude", "E1", 0.95), "CHARLIE": answer()}))
    run_id = uuid.uuid4()
    handlers.handle_seed_false_exclusion_check(_claimed(world, run_id, monkeypatch))
    latest = world["owner"].get(_url(world)).json()["latest"]
    assert latest["id"] == str(run_id) and latest["model_id"] == "test-model" and latest["prompt_version"] == "screening_prescreen@1"
    assert latest["summary"]["excluded"] == 1 and latest["summary"]["rough"] is True and len(latest["entries"]) == 4
