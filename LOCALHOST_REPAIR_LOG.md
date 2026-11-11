# Localhost repair log

## 2026-10-29 — local server startup

- **Observed:** normal `uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000` imports the app but does not complete startup within 70 seconds.
- **Evidence:** an import diagnostic completed successfully after roughly 27 seconds. The remaining delay is in the FastAPI lifespan path, which conditionally runs `Base.metadata.create_all(bind=engine)` against the configured local database.
- **Temporary local-host action:** start Uvicorn with `--lifespan off` so the static UI can be served while the configured database startup issue is investigated.
- **Code or configuration changes:** none. `.env` was not read or modified.

## 2026-10-29 — execution-environment limitation

- **Observed:** port 8000 rejects a local health-check connection with Windows socket error 10013 (access permissions). A plain Python HTTP server can bind to port 8010 in an interactive terminal, but the Codex execution terminal does not expose that long-running child process to a separate localhost probe.
- **Result:** no durable local app process could be started from this execution environment. No application code, database, or configuration was changed while testing it.

## 2026-10-29 — live search-stage smoke test

- **Context:** authenticated project `d70c5117-39c0-4b80-bd04-554f17e36a17`, Search stage at `localhost:8000`. OpenAlex showed **Ready** and G2 Search plan showed **Approved**. Other database connectors showed Off or Not built.
- **Passed:** the new-search dialog rendered; submitting an empty concept was blocked by required-field validation. A bounded OpenAlex search for `gender budgeting` with max results `5` displayed `Search queued.` and persisted after reload.
- **Issue S1 — queued search did not progress during the smoke test:** after reload the search appeared under **Pending** as `v1 · Openalex — Queued`; no results or failure explanation appeared in the UI. Verify whether the separate worker process is running and whether it claims this task. This observation does not establish a worker defect by itself.
- **Issue S2 — search plan completeness warning:** Criteria & seeds showed `No criterion yet for: comparison, outcome` while G2 was Approved. Confirm whether these PICO fields are intentionally optional for this project; if required, approval validation is too permissive.
- **Scope limit:** downstream screening and extraction were not tested because the search had not produced sources. No gate was reopened or approved during this test.

## 2026-10-29 — stage and UI mechanism review

- **Verified in code:** the server supplies the project-specific stage chain; the UI groups work into Plan, Gather, Synthesize, and Project. A stage advance checks stale artifacts, its human gate, and required/recommended exit conditions before changing the stage. The separate worker claims queued tasks; an approved gate alone does not execute them.
- **Issue R1 — misleading advance explanation:** `backend/app/web/js/steps/common.js` renders every `option.ready === false` as `Needs gate ... approved`, even when the gate is already approved and an exit condition or stale result is the blocker. For an ungated option it can display `Needs gate null (undefined)`. The stage API computes conditions, but this card does not explain them. This is a code-review finding; it was not reproduced in the live browser.
- **Issue R2 — pending search offers little diagnosis:** `backend/app/web/js/steps/search.js` displays pending task status and gate blocking only. It does not show a failure reason, worker health, or the last update time. A queued task with a stopped worker therefore looks like normal progress. This is a usability finding supported by the earlier live queued-search observation.
- **Live review limit:** this session could not attach to the current in-app browser (`browser.user is unavailable`), and a shell health probe to `localhost:8000` returned Windows socket error 10013. The Activity page and later stages were not interacted with in this pass; no claim about their live behavior is made.
