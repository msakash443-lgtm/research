# X.185 — Redundancy-by-coverage and source-text-pin review

Read-only review (rule 44: deletions need owner approval; owner approved in this
assignment, but conservative calls below are still deferred to a human as `needs owner`).
No deletions were made (see Part (b) conclusion); no code was changed.

## Part (a) — coverage-context redundancy

**Blocked on tooling.** `pytest-cov` / `coverage` is not installed in `.venv`:

```
.venv/Scripts/python.exe -c "import pytest_cov"
ModuleNotFoundError: No module named 'pytest_cov'
```

`pip list` (60 packages) has no `pytest-cov` or `coverage` entry. Per instructions, this
agent did not install it. `pytest --cov=app --cov-context=test` cannot run without it, so
no coverage-redundancy candidates could be produced. **Someone with permission to add
`pytest-cov`/`coverage` to `backend/requirements-dev.txt` needs to install it and re-run
this half of the task** (full serial run ~33 min on the Postgres harness, or `-n 4` with
per-worker DB names as conftest already provides).

## Part (b) — source-text / repo-file-reading test review

24 files matched `read_text|getsource|__file__` in `backend/tests/*.py` (23 test files +
`conftest.py`, which is out of scope — it's shared fixture code, not a test). `conftest.py`
not reviewed (shared setup, not a pinned-wording test).

One of the matched files, `test_g3_status_guard.py`, is on X.182's concurrent-edit list;
left untouched and marked **deferred** below without a keep/delete judgment being acted on.

| File | Test(s) using read_text/getsource/`__file__` | Verdict | Reason |
|---|---|---|---|
| `test_g3_status_guard.py` | all (plan.md text) | **deferred** | X.182 is editing this file concurrently; not reviewed for action, left alone |
| `test_compose_hardening.py` | all 3 | keep | real invariant: Postgres published loopback-only, password has no hardcoded fallback, every service that uses the password requires it — docker-compose.yml behavior, not prose |
| `test_requirements_split.py` | all 3 | keep | real invariant (X.141): web image must not ship ARC/AGPL packages or pytest; AST-scans `app/` imports, not wording |
| `test_migration_heads.py` | all 5 | keep | real invariant: single Alembic head, no duplicate/renumbered revision ids — exactly the example the card cites to keep |
| `test_plan_status.py` | n/a (loads `scripts/plan_status.py` as a module, not reading its text) | keep | behavioral test of the script's `summarise()` output against an inline sample, not a source-text pin |
| `test_prompts.py` | checksum + golden-render tests | keep | intentional, documented invariant: published prompt versions are immutable (checksums), rendered text matches a captured golden fixture — the design explicitly calls for pinning, not an incidental one |
| `test_eval_baselines.py` | reads `BASELINES`/gold-set JSON | keep | data-driven rule checks (gold set / baseline consistency), not wording |
| `test_stage_order.py` | `test_no_module_outside_stage_order_iterates_the_stage_enum_for_ordering` | keep | single-point-of-control static guard (only `stage_order.py`/`models.py` may order `ProjectStage`), same class as the "single Alembic head" example |
| `test_test_hygiene.py` | both tests (AST scan of `tests/`) | keep | this *is* the X.186 regrowth guard; not reviewed for deletion (out of this card's scope, it's the meta-test X.186 will formalize) |
| `test_cors.py` | subprocess probe only (`__file__` used to locate `backend/` for `PYTHONPATH`, not to read source) | keep | behavioral HTTP preflight test; `__file__` use is incidental path-finding |
| `test_adversarial.py` | `test_the_front_end_never_renders_output_as_html` | keep | real XSS-sink invariant across `app/web/*.js` (`innerHTML`, `eval(`, etc.), not wording |
| `test_arc_fencing.py` | `test_research_main_never_imports_arc_code_or_uses_its_context_strings` | keep | rule-26 boundary: no `researchclaw` import / `to_context_string` use in `app/` — architectural fence, not prose |
| `test_citation_verifier.py` | `test_the_verifier_never_touches_the_database_or_marks_anything_verified` | keep | real invariant: verifier module must not import sqlalchemy/app.models or assign `metadata_verified =` — enforces a design boundary, not wording |
| `test_connector_interface.py` | `test_connector_package_never_touches_the_database_or_imports_arc` | keep | AST-scans `app/connectors/*.py` imports for a forbidden set — architectural boundary |
| `test_extraction_verification.py` | `test_source_guard_is_registered_when_only_models_are_imported_in_a_worker_process` | keep | behavioral subprocess check that an ORM `before_insert` hook is registered; `__file__` only locates the subprocess cwd |
| `test_gate_decisions.py` | `test_no_agent_or_worker_code_can_decide_a_gate`, `test_nothing_in_the_app_approves_a_gate_except_the_decision_function` | keep | gates-are-human-only invariant: no automated code reaches `decide_gate`/sets `GateStatus.approved`/`rejected`; single setter is `routers/gates.py` |
| `test_gated_tasks.py` | `test_only_the_gate_decision_route_may_release_tasks_for_a_gate` | keep | single-caller guard: only `routers/gates.py` calls `release_tasks_for_gate` |
| `test_gated_tasks.py` | `test_the_worker_loop_releases_and_runs_tasks` (`inspect.getsource(worker.run_forever)`) | **needs owner** | checks literal substrings `release_gated_tasks`/`run_one_task` appear in the function body — pins incidental implementation rather than behavior; no existing behavioral test proves `run_forever` actually invokes both, so deleting outright would lose coverage. Recommend replacing with a behavioral test (monkeypatch both and assert they were called) rather than a straight delete; left as `[?]`-style flag for a human decision, not deleted |
| `test_gold_set.py` | all jsonschema validations | keep | data-shape validation against `gold_set.schema.json`, not wording |
| `test_guidance.py` | `test_a_guide_missing_a_stage_fails_loudly`, `test_an_answer_pointing_to_an_unknown_path_fails_loudly` | keep | behavioral tests that mutate the real `journey.toml` text to prove the loader fails loudly on structural defects — not pinning its prose, pinning the fail-loudly behavior |
| `test_prescreen.py` | `test_the_ai_never_writes_a_human_decision_or_counts_as_decided` (checks `'decided_by="human"'` absent from `app/prescreen.py` source) | keep | real invariant: the AI-only module must never literally write the human-decision sentinel; small, behaviorally-grounded static guard (same class as the gate/stage single-setter guards) |
| `test_search_gate.py` | `test_only_the_gated_handler_runs_searches` | keep | single-caller guard: only `task_handlers.py` references `search_runner`, and it requires gate G2 |
| `test_source_verification.py` | `test_only_the_person_route_and_the_verifier_can_set_metadata_verified` | keep | single-setter guard: only `source_verification.py`/`sources.py` assign `metadata_verified = True`; `task_handlers.py` must call `verify_source(` instead |
| `test_stage_advance.py` | `test_only_the_state_machine_and_re_entry_assign_a_projects_stage` | keep | single-setter guard: only `artifacts.py`/`stage_machine.py` assign `.stage =` |
| `test_task_runner.py` | `test_the_worker_loop_runs_tasks_and_no_longer_claims_runs_directly` (`inspect.getsource`) | **needs owner** | checks literal substrings in `run_forever`/`_reaper_loop` source (`run_one_task` present, `reap_expired_tasks` absent from the main loop, present in the reaper thread; `claim_next_run`/`reap_stale_runs` absent — the latter two look like guards against reintroducing removed legacy function names). The X.115 reaper-on-its-own-thread invariant this guards is *also* proven behaviorally by `test_a_hung_handler_does_not_stop_its_expired_task_being_reaped` in the same file, so this source-text half is likely redundant with that behavioral test; the two now-absent-function checks may be stale guards for APIs that no longer exist anywhere in the codebase. Candidate for deletion/trim, but left to a human: deleting the `reap_expired_tasks not in source` assertion removes the only check that the hung-handler fix (X.115) didn't regress into the old single-thread shape if the behavioral test is ever weakened |

## Deletions

**None.** Every file in the 23 reviewed either tests real, behavior-grounded invariants
(a single Alembic head, no ARC import, gates/stage/metadata-verified single-setter guards,
no-XSS-sink, prompt-version immutability by design) or is intentionally out of this card's
scope (`test_g3_status_guard.py`, deferred for X.182; `test_test_hygiene.py`, itself the
X.186 regrowth guard). Two tests (`test_gated_tasks.py::test_the_worker_loop_releases_and_runs_tasks`,
`test_task_runner.py::test_the_worker_loop_runs_tasks_and_no_longer_claims_runs_directly`)
border on pinning incidental implementation via `inspect.getsource` string checks rather
than behavior; flagged `needs owner` above instead of deleted, since removing them would
leave partially-untested regressions and no replacement behavioral test was written in
this pass.

No files were changed, so no test run is required for this review (no deletions to verify).
