# Localhost end-to-end validation log

## 2026-11-01 — X.16 local demo run

Scope: user-authorized localhost workflow validation using a synthetic development account and a clearly labelled demo project. This log records runtime findings and any files changed during this run. No gate was approved by the agent, and no source was marked verified without a human decision.

### Setup

- Docker Compose services `db`, `web`, and `worker` were healthy.
- Signed in through the development-login UI as the synthetic local account `codex.local@example.com`.
- Created project **Local workflow paper trial** with a validation-only description.

### Findings before the demo topic

| Step | Outcome |
|---|---|
| Development login | A syntactically invalid `.test` address returned HTTP 422; a valid `example.com` synthetic address succeeded. |
| Overview, Scope, Criteria, Search, Results, Sources, Ask | Each loaded against the live Docker/PostgreSQL stack. |
| Search | OpenAlex was available; other connectors were correctly shown as disabled or unavailable. |
| Results | Correctly stated that no results exist until a search is run. |
| Sources / Ask | Correctly require saved evidence and verified citations before a synthesis can be produced. |

### Files changed by this run

- `plan.md`: claimed X.16 for this user-authorized validation.
- `LOCALHOST_E2E_LOG.md`: created this runtime log.

### Browser-control recovery

- The original in-app browser tab became unavailable to automation (`browser.user is unavailable`).
- On retry, the browser had no remaining in-app tabs. A new visible localhost tab opened successfully and restored the authenticated validation session.
- Current observed state: the Overview page loads; G1 Scope is pending; no research question has been saved yet.

### Demo workflow execution

- Saved topic/question: **How does remote work affect employee well-being and productivity among knowledge workers?**
- Saved an explicit validation-only rationale, so the question is not represented as an evidence-backed conclusion.
- Saved PICO criteria:
  - Include: knowledge workers in remote or hybrid work arrangements.
  - Exclude: studies without a reported well-being or productivity outcome.
- Created an OpenAlex search request with the concept `Remote work` and the terms `remote work`, `telework`, and `work from home`.
- Expected gate result: the saved search is `Pending — Waiting for G2 (Search plan) approval`; G2 correctly states that it waits for G1. No gate was approved by the agent and no retrieval ran.

### Errors

- No application error occurred in this execution segment. The pending search is an expected integrity control, not an error.

### Human-approval UI inspection

- The Approvals screen states: **“Only a person can approve a gate.”**
- G1 Scope is the only actionable pending gate. G2 Search plan explicitly waits for G1; G3–G11 show their ordered prerequisite gates.
- The saved remote-work question appears in the project research-memory panel.
- No approval or rejection was submitted by the agent.

### Human approval observed

- G1 Scope was visibly approved by `Local Workflow Tester` on 2026-11-01 at 2:56 PM (local UI time).
- G2 Search plan became the next actionable human gate; G3–G11 remain blocked behind G2 and their other listed predecessors.
- The agent did not submit the G1 approval.

### G2 approval and live search result

- G2 Search plan is visibly approved and the queued OpenAlex search completed: 500 identified, 500 unique, 0 duplicates removed.
- **Runtime defect:** Results check renders all 500 returned records as `Untitled` with no publication year, while citation-count values are present. The result-cap warning is also shown, so the 500 count is a lower bound rather than a complete search result.
- This is an observed live-Docker/UI issue; no attempted fix was made in this validation pass.

### G3 prerequisite check

- Sources screen: **0 evidence sources**. The 500 OpenAlex result records were not available as usable source records for verification.
- Screening screen: **To screen: 0; Included: 0; Excluded: 0; Maybe: 0**. It presents “Nothing left to screen,” but there is no corpus to have screened.
- Conclusion: do **not** approve G3. There is no verified/usable metadata and no screening decision set. This is blocked by the imported-results/source-import defect noted above.

### Repair started

- User requested retrieval and repair of the `Untitled` records. X.153 was claimed to trace the completed-search record schema, import path, and result-card mapping. G3 remains a human-only approval and will not be approved during repair.

### 2026-11-01 — X.153 retrieval-metadata repair

#### Root cause and repair

- Cause: `search_runner._summarise()` retained only identifiers, DOI/work keys, and citation counts in `SearchQuery.results`; it discarded titles, authors, years, abstracts, and venue metadata. The completed-search path also did not create `Source` records, so the Sources and Screening screens had no corpus.
- Repair: completed searches now retain the normalised record metadata in their saved result snapshot and create idempotent, `origin=retrieved`, `metadata_verified=false` source records. Citation count provenance and search provenance are retained. This makes retrieval reviewable but does **not** verify a record, auto-screen it, or approve G3.
- Changed files: `backend/app/search_runner.py`, `backend/tests/test_search_runner.py`, `backend/tests/test_citation_counts.py`, `plan.md`, and this log.

#### Verification

- `backend/tests/test_search_runner.py`: **13 passed**.
- `backend/tests/test_citation_counts.py -k run_stores_each_results`: **1 passed, 15 deselected**.
- `node --test backend/app/web/js/test/results.test.mjs`: **4 passed**.
- The broader citation-count file did not finish within the 30-second command window (only progress dots appeared); no pass result is claimed for that run.

#### Local deployment incident

- A plain `docker compose up -d --build web worker` attempted to bind PostgreSQL to `127.0.0.1:5432` and failed because the local stack requires the existing `docker-compose.local.yml` override (`127.0.0.1:15432:5432`).
- Restarting with the override briefly showed the empty-project state. No database reset or deletion command was issued in this repair session. After the app settled, the existing synthetic project and completed search were available again; the empty state was transient, not evidence of data loss.
- Do not approve G3 until the human screening decisions are complete.

#### Live re-retrieval validation

- After the stack recovered, the existing synthetic project and its approved G2 search were again available. The agent re-queued version 2 of that already human-approved OpenAlex query; it did not approve or reopen any gate.
- The worker completed the re-run with 500 unique records. The Sources screen now visibly lists retrieved records with real titles, years where supplied, DOI/URL links, citation counts and explicit **AUTO-RETRIEVED, UNVERIFIED** status. Examples observed include “Teleworking in the COVID-19 pandemic: Trends and prospects” (2021) and “Does Working from Home Work? Evidence from a Chinese Experiment” (2014).
- The prior `Untitled`/empty-Sources defect is corrected in the deployed local stack. Records remain unverified and unscreened; the agent made no screening decision and did not approve G3.

### G3 human approval reported

- The user reported that G3 was approved after completing screening. The next workflow screen is Evidence table.
- A newly opened browser tab intermittently loaded the empty-project state instead of the existing project, so the agent could not independently re-confirm this gate in that fresh tab. No gate action was performed by the agent.

### Post-G3 service check (2026-11-01)

- The existing in-app-browser tab remains addressed to the project Evidence stage: `#/p/67c79eaf-f611-4958-838e-0fd850740d4f/evidence`.
- The local readiness endpoint returned `{"status":"ready","database":"available"}`.
- Browser automation could list the tab but could not attach to its interactive session (`browser.user is unavailable`), so the rendered Evidence-table contents and gate banner could not be independently inspected in this check. This is recorded as a browser-control limitation, not a gate failure. No gate, screening, or evidence record was changed by the agent.

### Post-G3 workflow check (2026-11-01)

- The defined next project stage after `SCREENED` is `EXTRACTED` (the Extraction / Evidence review workflow).
- The transition is protected by G4 and its extraction-review exit conditions. G3 approval does not authorize skipping G4, so no stage-advance request was made and no gate was changed by the agent.
- The interactive in-app-browser session remains unavailable to automation; this check used the locally deployed workflow rules rather than an unverified UI click.

### Evidence-table inclusion filter defect confirmed (2026-11-01)

- The Evidence table is currently assembled from `clusterable_sources`, which removes excluded records but retains every record that is not excluded. It does **not** require an explicit final `include` decision.
- Therefore the user-observed 500 records can legitimately appear even though the intended extraction workflow should operate on only the explicitly included studies. This is a confirmed product defect, not an extraction result.
- No code or project data was changed in this confirmation. A corrective change should filter the Evidence table (and any downstream extraction workflow) to sources with a final inclusion decision, preserving dual-screen/adjudication semantics.

### Evidence filtering and extraction action deployed (2026-11-01)

- Implemented the final-inclusion filter: the Evidence table, G4 verification queue, and extraction-write paths now require a settled `full_text` `include` decision. Unscreened, title/abstract-only, `maybe`, and excluded records cannot enter the evidence base or start an extraction.
- Added a per-paper **Start AI extraction** action in the Evidence table for a final included paper with a stored full-text PDF. It queues the existing evidence-grounded extraction task; it does not mark the result verified or bypass human review/G4.
- Rebuilt and restarted the local `web` and `worker` containers with the local compose override. A first readiness request ended prematurely during startup; the one permitted retry returned `{"status":"ready","database":"available"}`.
- `compileall` and `node --check backend/app/web/js/steps/evidence.js` completed without errors. Focused pytest runs for the affected test modules were attempted, but the runner reached collection/execution and produced no completion result within its 30-second window; no pytest pass result is claimed.

### Files changed by this run

- `plan.md`: claimed X.16 for this user-authorized validation.
- `LOCALHOST_E2E_LOG.md`: created and updated this runtime log.

### AI extraction action and storage (2026-11-02)

- The Evidence table now always shows **Start AI extraction** for an editable, final-included paper that has not already been extracted. It is disabled with “Store a full-text PDF to enable AI extraction” until the stored-PDF coverage is present. When enabled, it queues `POST /extractions/ai`; it never creates a manual extraction.
- The worker now uses the project-wide AI controls, so an AI-Off project cannot queue this work and a local-model route does not silently fall back to a third-party model. An AI result is persisted as `extracted_by="ai"`, retains model/prompt provenance, and remains unverified until human review.
- Verification: focused Evidence-table Node test: **4 passed**; full frontend suite: **217 passed**; application bytecode compilation and `git diff --check` passed. The focused `test_ai_extraction.py` pytest command produced seven progress dots but no completion summary before the 30-second runner limit. Full pytest was not completed because the Windows sandbox stalls FastAPI TestClient while creating its asyncio socketpair. No gate, screening decision, or live extraction was changed during this check.
- Deployment: rebuilt only the local `web` and `worker` services with the local compose override. `/ready` returned `{"status":"ready","database":"available"}`; the served Evidence module contains `aiExtractionAction`; `web` is healthy and `worker` is running.

### Evidence-table PDF upload (2026-11-02)

- Added a **Upload PDF** file picker beside a final-included, unextracted paper whose full text is not yet stored. It accepts a selected PDF, sends its raw bytes only to the existing audited `.../fulltext/upload` endpoint, then reloads the Evidence table. A successful upload changes the same row to stored full-text coverage and enables **Start AI extraction**.
- The existing endpoint is write-once and validates size, PDF signature, readable text layer, project membership, storage result, and audit metadata. It does not auto-start extraction and does not replace a PDF that is already stored.
- Verification: upload-specific frontend tests **2 passed**; Evidence logic tests **4 passed**; full frontend suite **219 passed**. Existing `test_fulltext_upload.py` was attempted but all 15 tests failed during pytest setup because the Windows sandbox could not create its `pytest-of-msaka` temporary directory; a repository-local retry did not report a result within 30 seconds. This is recorded as an environment limitation, not a claimed backend pass. Local rebuild succeeded; `/ready` reports the database available and the served module contains **Upload PDF**. No user PDF, extraction, decision, or gate was changed.

### 2026-11-04 — fresh SQLite E2E smoke after reset

- Reset setup: local SQLite database was re-created and verified empty before this pass. The prior file was retained separately as a recoverable backup.
- Passed: development login, empty-workspace state, project creation, Scope navigation, research-question save, Sources navigation, and synthetic source save. The question and source were visibly present after the page's asynchronous refresh.
- Integrity controls: G1 Scope remained pending; G2 and later gates remained unavailable. The synthetic source remained **unverified**. No gate approval, source verification, screening decision, retrieval, or extraction was submitted by the agent.
- Investigation: immediately after the two saves, the browser automation's first snapshot still contained the prior empty/checklist content. The application code already waits for `refresh()` after both mutations; a subsequent fresh observation showed the saved question/source. This is recorded as an automation timing artifact, not a product defect; no UI code was changed.
- Runtime limitation: this host process has no configured local model and Ollama is not listening on port 11434, so the newly added API-to-Ollama fallback cannot be live-exercised here. The application correctly leaves local-only routing unavailable rather than silently using another model.

### 2026-11-04 — continued search and source-ingestion check

- The user-approved G2 was already shown as approved in the browser. A bounded OpenAlex query for `"evidence synthesis"` was queued with a maximum of five results; no gate was approved, reopened, or changed by the agent.
- Initial observation remained `Queued` because the host-mode Uvicorn server does not include a worker. A detached worker launch exited immediately in this local shell setup; running the same worker command with `PYTHONPATH=backend` in a retained process completed the queued job. This is a local process-launch limitation, not a product error.
- Passed: Search now shows version 1, five results, five unique, and zero duplicates removed. Sources shows all five imported records as **AUTO-RETRIEVED, UNVERIFIED**, with provenance, citation counts, and the explicit warning that the agent cannot cite them. No imported record was verified, screened, fetched, attached, or extracted.
- The pre-existing synthetic source remains unverified. This check stops at the human-controlled source-review/screening boundary.

### 2026-11-04 — post-screening stage check

- The user reported inclusion and exclusion screening complete. The live project is visibly at **Synthesize → Evidence table**, confirming the project has advanced beyond Screening through its permitted workflow.
- The table contains three included papers. Each is still not extracted; AI extraction remains disabled until a full-text PDF is stored. No source verification, PDF upload, extraction, or subsequent gate action was performed by the agent.

### 2026-11-05 — local-host availability check

- Local FastAPI host was started successfully on `http://127.0.0.1:8001` because port 8000 was already unavailable (`WinError 10048`). The browser loaded the authenticated project overview and displayed the persisted research path: saved scope, criteria, search/results, six sources, screening, Evidence table, verification, Ask, and Write.
- This confirms application startup, session restoration, and navigation of the saved E2E project. No project data, gate, source status, screening decision, upload, or extraction was changed.

### 2026-11-05 — single-PDF extraction check

- User uploaded a full-text PDF for “Bayesian methods in meta-analysis and evidence synthesis” and submitted the existing single-paper extraction action. The task was queued, then processed by the local worker.
- Result: `ai_extraction` failed after one attempt with the non-secret error: **“The configured LLM service could not be reached.”** The project route is `standard` with suggestions-only AI. No extraction rows or placeholder output were written; the Evidence table remains correctly marked **Not extracted** and the action is available to retry after service repair.
- PDF parsing emitted `pypdf` font-encoding warnings because optional `fontTools` is not installed. Those warnings did not stop PDF storage; the failure occurred when the configured LLM service was contacted. No key was read or exposed.

### 2026-11-05 — effective LLM configuration check

- Effective non-secret settings: standard API key is present; base URL is `https://api.openai.com/v1`; selected standard model is `gpt-5.6-luna`; no local/Ollama fallback endpoint or model is configured. The project uses the standard suggestions-only route.
- The extraction task's transport failure therefore cannot be recovered by the current fallback policy. Retry is deferred until the standard model is made reachable or a local fallback is configured; repeating the same request now would produce the same failure and unnecessary work.

### 2026-11-05 — API-only model routing

- Per user direction, standard project AI now uses only `LLM_API_BASE_URL` / `LLM_MODEL`; it never retries through Ollama or any configured local endpoint. A mocked transport check observed exactly three API-host retry attempts and no local-host call.

### 2026-11-05 — OpenAI extraction failure diagnosis

- Non-generation verification of the configured `https://api.openai.com/v1/models/gpt-5.6-luna` endpoint returned HTTP 200, confirming the key and model access path are valid.
- A one-token request using the application's `chat/completions` payload returned HTTP 429: **“You have no credits remaining. Add credits to continue using the API…”** The extraction failure is therefore an account billing/credit condition, not an Ollama, base-URL, key-presence, or model-name error. No PDF extraction output was generated or stored.

### 2026-11-05 — API 429 verification and repair

- The response was rechecked with the API's current output-token parameter: `error.type=insufficient_quota`, `error.code=credit_balance_exhausted`, no Retry-After header. This excludes temporary rate limiting and confirms the key's organization has no available prepaid API credit.
- The adapter previously collapsed this billing response into “service could not be reached.” It now stops immediately for credit/spend-limit codes and returns a safe actionable message; no error body, request ID, or credential is persisted. Focused retry tests: 18 passed.
