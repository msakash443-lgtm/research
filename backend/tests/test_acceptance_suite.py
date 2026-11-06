"""Plan X.10 / spec §13: one acceptance-level test per end-to-end criterion in
`docs/research-automation-spec.md` §13 (criteria 1-6), run as part of the normal `pytest -q` suite
(`testpaths = backend/tests` in pytest.ini already covers this file).

Each test exercises the real FastAPI `TestClient` + the real SQLite test DB, the same way every other
API test in this package does. Where a focused test already proves the claim in detail, this file calls
that test directly (or reuses its fixtures) and adds only the acceptance-level assertion, instead of
re-testing internals a second time:

  §13.1 idea -> verified evidence table + ranked gaps, full search log, PRISMA  -> test_m3_exit.py (M3.13.1)
  §13.2 every citation resolves; unverifiable cannot be exported               -> test_citation_guard.py (M1.10.3),
                                                                                    test_manuscript_export.py (M6.4)
  §13.3 every number traceable to an analysis run                             -> test_number_link_check.py,
                                                                                    test_manuscript_export.py (M6.3)
  §13.4 every AI artifact shows who/what/when/model + approval status         -> test_synonym_suggest.py,
                                                                                    test_run_read_fields.py (M0.4, M0.8.1, X.17)
  §13.5 re-enter any stage; downstream work flagged stale                     -> test_reentry.py (M0.5.5)
  §13.6 one-action audit/replication package                                  -> test_replication_export.py (X.8.1)
"""
import json

import pytest
from fastapi.testclient import TestClient

from app.agent.llm import OpenAICompatibleLLM
from app.config import get_settings
from app.main import app

from tests.llm_replies import structured
from tests.test_m3_exit import test_m3_exit_question_search_screen_verified_evidence_and_ranked_gap as _m3_exit
from tests.test_manuscript_export import world as _export_world_fn
from tests.test_manuscript_export import (
    test_a_clean_export_is_not_blocked as _export_clean,
    test_export_is_blocked_by_a_citation_to_an_unverified_source as _export_blocked_by_unverified_citation,
    test_markdown_and_docx_render_a_resolved_inserted_number as _export_renders_resolved_number,
    test_export_is_blocked_by_a_number_whose_path_does_not_resolve as _export_blocked_by_unresolved_number,
)
from tests.test_reentry import world as _reentry_world_fn
from tests.test_reentry import (
    test_re_entry_marks_the_target_and_later_stage_artifacts_stale_and_records_why as _reentry_marks_stale,
    test_the_rerun_path_groups_stale_work_by_stage_in_order_with_each_gates_status as _reentry_rerun_path,
)
from tests.test_replication_export import world as _replication_world_fn
from tests.test_replication_export import download as _download_replication

# Plain (unwrapped) factory functions for each source module's `world` fixture, so each acceptance test
# below can build as many independent `world`s as it needs (a shared instance across two reused
# sub-tests would collide on unique per-project rows such as the sample dataset version).
new_export_world = _export_world_fn.__wrapped__
new_reentry_world = _reentry_world_fn.__wrapped__
replication_world = pytest.fixture(name="replication_world")(_replication_world_fn.__wrapped__)


def test_acceptance_13_1_idea_to_verified_evidence_table_with_search_log_and_prisma(monkeypatch):
    """§13.1: a scholar can go from a rough idea to a verified evidence table and ranked gap list, with a
    complete search log and PRISMA counts. Runs the real M3 exit path end-to-end (M3.13.1); see
    `test_m3_exit.py` for the detailed walk-through this acceptance claim is drawn from."""
    _m3_exit(monkeypatch)


def test_acceptance_13_2_every_citation_resolves_unverifiable_cannot_be_exported():
    """§13.2: every citation in generated text resolves and matches its source; an unverifiable reference
    cannot be exported. A clean manuscript (every citation verified) exports; the same project with one
    citation to an unverified source is hard-blocked (409), not silently exported or merely warned."""
    _export_clean(new_export_world())
    _export_blocked_by_unverified_citation(new_export_world())


def test_acceptance_13_3_every_number_traceable_to_an_analysis_run():
    """§13.3: every number in the manuscript is traceable to a stored `AnalysisRun`. A number whose path
    resolves against a real run renders the real computed value on export; a number whose path does not
    resolve in that run's stored output is hard-blocked at export (409), never silently rounded or guessed."""
    _export_renders_resolved_number(new_export_world())
    _export_blocked_by_unresolved_number(new_export_world())


def test_acceptance_13_4_every_ai_artifact_shows_producer_model_date_and_approval(fake_llm, monkeypatch):
    """§13.4: every AI-generated artifact shows who/what produced it, which model, when, and its human
    approval status. Exercised on the shared AI-suggestion system (X.46/X.17, the mechanism behind every
    AI proposal in the app except research-run free-text answers): the proposal carries model/prompt/
    producer provenance before any decision, and an explicit accept records who approved it and when.
    A research-run answer is also checked directly for producer/model/date (it has no approval step of
    its own yet; see the gap noted in this file's test report / plan.md X.10)."""
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": "acc134@example.com", "display_name": "Acc"})
    project_id = client.post("/api/projects", json={"title": "Acceptance 13.4"}).json()["id"]

    fake_llm.handler = lambda r: {"choices": [{"message": {"content": json.dumps(
        {"suggestions": [{"term": "working from home", "reason": "common phrase"}]}
    )}}]}
    body = client.post(f"/api/projects/{project_id}/searches/suggest-synonyms",
                        json={"block": {"label": "work", "terms": ["remote work", "telework"]}}).json()
    suggestion_id = body["proposals"][0]["suggestion_id"]

    # Producer, model, prompt version are recorded before any person decides anything.
    why = client.get(f"/api/projects/{project_id}/suggestions/{suggestion_id}/why").json()
    assert why["model_id"] == "fake-model" and why["prompt_version"] == "synonym_suggestions@1"
    assert why["producing_agent"]
    listed = client.get(f"/api/projects/{project_id}/suggestions",
                         params={"target_kind": "synonym", "target_id": body["target_id"]}).json()
    assert listed[0]["state"] == "pending" and listed[0]["accepted_by"] is None and listed[0]["accepted_at"] is None

    # A person's approval decision is recorded with who and when.
    accepted = client.post(f"/api/projects/{project_id}/suggestions/{suggestion_id}/accept").json()
    assert accepted["state"] == "accepted"
    assert accepted["accepted_by"] and accepted["accepted_at"]

    # Research-run answers show producer/model/date too (the run record itself), even though they have
    # no approval step of their own yet.
    source_id = client.post(f"/api/projects/{project_id}/sources", json={
        "title": "S", "evidence_excerpt": "Participation rose.",
    }).json()["id"]
    client.post(f"/api/projects/{project_id}/sources/{source_id}/verify")
    monkeypatch.setattr(get_settings(), "llm_model", "test-model")
    monkeypatch.setattr(OpenAICompatibleLLM, "complete_json",
                         lambda self, system, user, schema, max_attempts=None: structured("Answer [S1]."))
    run = client.post(f"/api/projects/{project_id}/research-runs", json={"question": "What changed?"}).json()
    assert run["provider_model"] == "test-model" and run["created_by"] and run["status"] == "completed"
    listed_run = client.get(f"/api/projects/{project_id}/research-runs").json()[0]
    assert listed_run["created_at"]


def test_acceptance_13_5_reentering_an_earlier_stage_flags_downstream_work_stale():
    """§13.5: the scholar can re-enter any earlier stage; downstream artifacts are flagged stale with a
    clear re-run path, not silently left to look current."""
    _reentry_marks_stale(new_reentry_world())
    _reentry_rerun_path(new_reentry_world())


def test_acceptance_13_6_full_audit_replication_package_exports_in_one_action(replication_world):
    """§13.6: a full audit/replication package (every project record, search log, PRISMA counts, screening
    decisions, extractions, research runs, audit log, checksummed manifest) is exportable in one request."""
    archive = _download_replication(replication_world)
    names = set(archive.namelist())
    assert "manifest.json" in names and "README.md" in names and "derived/prisma.json" in names
    assert any(name.startswith("data/") and name.endswith("sources.json") for name in names)
    assert any(name.startswith("data/") and name.endswith("screening_decisions.json") for name in names)
    assert any(name.startswith("data/") and name.endswith("audit_events.json") for name in names)
