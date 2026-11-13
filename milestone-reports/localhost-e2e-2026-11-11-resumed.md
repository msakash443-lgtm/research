# Localhost end-to-end diagnostic — resumed — 2026-11-11

**Result: localhost runs and the evidence workflow reaches the live model API, but no research answer was produced.** The configured API reports exhausted credits, so no successful model inference is demonstrated. The configured local Ollama endpoint is unreachable. The app preserves a failed run with no answer, rather than storing fabricated output.

| Field | Value |
|---|---|
| Task / tester | X.227 / @codex |
| Checkout | `Revathi`, `27466d70e8e1954a201f595d5e95fda54c9f0082` |
| Date | 2026-11-11, Asia/Calcutta |
| URL | http://127.0.0.1:8000/ |
| Spec exit criterion | Diagnostic only; no milestone exit or complete research lifecycle certified |
| Related checks | Evidence persistence, source verification, model failure handling, human gate enforcement, exports and audit |
| Application code changes | None; no new test applies to the documentation changes |

## 1. Runtime and exercised workflow

Built the web and worker images from this checkout. The first `docker compose up -d --build` could not bind PostgreSQL to Windows port 5432. Starting with the repository's existing `docker-compose.local.yml` succeeded, using loopback database port 15432 and its existing local-model configuration:

```powershell
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d
```

Web and PostgreSQL became healthy; the separate worker started. `/health`, `/ready`, root HTML, CSS and entry JavaScript returned HTTP 200. `/ready` also reported the migration warning documented below. The app, worker and database are left running for inspection.

The browser signed into the diagnostic account `localhost-e2e-20261111@example.com`, created **LOCAL E2E 2026-11-11 — hybrid work evidence**, saved a research question through Scope, and submitted a research run through Ask. Source creation and automatic metadata checking used the normal HTTP API; source status and its reading note were then verified in the browser. This is mixed browser/API end-to-end coverage, not a claim that every action was performed through the UI.

- Project: `82112c7b-3fad-40f9-ac64-73d00a712641`
- Source: `b7eb14ce-e0b7-47f1-8047-9262f92c156f`
- Source-check task: `4bb616a4-d77c-4689-a54b-67023bb50d76`
- Research run: `dd61c91a-588e-4f00-a4cd-9f8f755d9540`
- [Open the diagnostic run](http://127.0.0.1:8000/#/p/82112c7b-3fad-40f9-ac64-73d00a712641/ask)

The sample is Bloom, Han and Liang (2024), [Hybrid working from home improves retention without damaging performance](https://www.nature.com/articles/s41586-024-07500-2), DOI `10.1038/s41586-024-07500-2`. The saved evidence is explicitly labelled a paraphrased reading note from the abstract, not a verbatim quotation or human-approved extraction. OpenAlex automatically matched title, authors, year and DOI to `openalex:W4399567465`. The result was `metadata_verified=true`, `verification_method=automatic`. Its diagnostic metadata also lists an OpenAlex error entry; the match checks themselves all passed. No human verification endpoint was called.

| Live check | Observed result |
|---|---|
| Development login | Pass, HTTP 200; browser account displayed |
| Project creation and Scope question | Pass; persisted through fresh API reads and browser reload |
| Source and evidence-note persistence | Pass; source created HTTP 201 and visible after reload; live immutability was not tested |
| Worker automatic metadata verification | Pass; queued HTTP 202, then automatically verified |
| Research run submitted through browser | Reached live model API; inference blocked by exhausted credits |
| Failure provenance | Model `gpt-5.6-luna`, prompt `evidence_synthesis@5`, actor and timestamps persisted and displayed |
| Model answer / generated claims | No answer; zero claims. Not a successful synthesis |
| Unsigned access to project | HTTP 401 |
| Advance to Scoped with G1 pending | HTTP 409 with gate and required roles; stage remained `idea` |
| Human approval preservation | All 11 gates remained pending; none approved or rejected |
| BibTeX and RIS reference export | Pass, HTTP 200, one automatically verified reference, zero skipped |
| Obsidian vault export | HTTP 404: feature disabled in existing configuration |
| Audit | Ten events observed, including source creation/check/verification, context, queued/finished run and two reference exports |

## 2. Test results and evidence

- Frontend Node suite: **240 passed, 0 failed, 0 skipped**, logged in [resumed-node.txt](localhost-e2e-2026-11-11-resumed-node.txt).
- Full backend pytest: in progress at report creation; final result will be recorded before handoff. A separate PostgreSQL 16 container on loopback port 55439 uses a unique `localhost_e2e_20261111_test` prefix, generated in-memory credentials and temporary database storage. The live application database is not used by tests.
- No test was added because application behavior was not changed. The visual defect is reproducible in the browser and is recorded as separate work, X.229.
- Gold-set recall, screening agreement and extraction accuracy: **not measured**. This one-paper diagnostic is not a scholar gold-set evaluation.

Saved evidence:

- [API results, provenance, gate refusal and audit](localhost-e2e-2026-11-11-resumed-api.json)
- [Actual BibTeX download](localhost-e2e-2026-11-11-sources.bib)
- [Actual RIS download](localhost-e2e-2026-11-11-sources.ris)
- [Failed-run screenshot](localhost-e2e-2026-11-11-run-failure.jpg)
- [Automatically verified source screenshot](localhost-e2e-2026-11-11-verified-source.jpg)

The JSON is diagnostic evidence; it is not a built-in manuscript or vault export. No session cookies or credentials are saved in these artifacts. The pre-existing `local_e2e.cookies` was not read or changed. No PDF was retrieved or stored (`fulltext_path=null`, coverage `not_accessible`); screening and extraction were not exercised. Results apply to this local `Revathi` checkout and commit, not to any newer GitHub `main`; no pull or sync was performed.

## 3. Non-negotiables

| Check | Result and boundary |
|---|---|
| N1: citations resolve and unverifiable citations are blocked | Automatic metadata verification and verified-reference downloads passed. No completed answer existed to test answer citation click-through or rejection of unverified citations live. |
| N2: AI producer, model, date and approval are visible | Failed run displayed actor, model, prompt, timestamps and “Nothing to approve: this run produced no answer.” Successful-output provenance was not exercised. |
| N3: human gates block | G1 refusal verified live; all 11 remained pending. The other ten gate transitions were not exercised. |
| N4: earlier-stage changes mark downstream results stale | Not exercised; no completed downstream result. |
| N5: draft numbers link to analysis | Not exercised; no analysis run or manuscript created. |

## 4. Findings and remaining work

### X.229 — Duplicate stylesheet BOM breaks the theme

`backend/app/web/styles.css` begins with `efbbbfefbbbf3a726f6f7420`: two UTF-8 byte-order marks. The live CSSOM retains U+FEFF before `:root`, so that rule does not match the document root. Browser inspection returned an empty value for both `--ink` and `--navy`, and the root font was Times New Roman. Buttons depending on those variables can have white text on transparent backgrounds and appear absent. Screenshots capture the defect. The Node suite does not establish browser styling correctness. No CSS edits were made.

### X.230 — Neither configured model route currently supplies an answer

The standard-model run completed with status `failed`, `answer=null`, and:

```text
The configured LLM API account has no credits remaining. Add API credits, then retry.
```

The alternate local route is configured for `qwen2.5:latest` at the repository's existing Docker-to-host Ollama endpoint. A read-only `/v1/models` probe from the web container returned:

```text
httpx.ConnectError: [Errno 101] Network is unreachable
```

No Ollama executable was found on PATH or in the inspected standard installation directories. This does not prove it is absent everywhere. No model was downloaded or installed, no credits were purchased, and no API credential was changed. A working model is required before a genuine answer can be demonstrated.

### X.231 — Database migration tracking is behind

`/ready` returned HTTP 200 with database available, plus:

```json
{"behind":2,"db_revision":"20261029_0101","code_revision":"20261110_0103"}
```

Current settings enable automatic schema creation, and the inspected UI loaded, including its objectives section. A subsequent read-only SQLAlchemy schema inspection confirmed that both `landscape_scan_runs` and `research_objectives` exist, while `alembic_version` still contains `20261029_0101`. A healthy endpoint therefore does not establish migration consistency. The pending migrations unconditionally create these tables, so a blind upgrade risks duplicate-table errors; reconcile schema and data-copy requirements before changing the tracked revision. No migration, stamp, reset or downgrade was run against the app database. Normal application startup was allowed to run its configured automatic schema creation; this diagnostic did not capture a before-start table inventory.

### Export configuration

Obsidian export is disabled, which is expected for its default-off configuration. Its HTTP 404 is not evidence of an export implementation defect. BibTeX and RIS exports were obtained successfully.

## 5. Scope, side effects and deviations

No product specification was changed. No application code, secret file, dependency, Git branch or commit was changed. Runtime effects are the rebuilt/started existing Compose services, the separate test container, and the new diagnostic account/project/source/context/run/audit records. No existing research data was edited or deleted. The research workflow remains at Idea, with every human gate pending.

Documentation edits are limited to this report and supporting artifacts, the X.227 claim/findings/work-log entries in `plan.md`, and associated `docs/traceability.md` rows. Earlier diagnostic reports are retained unchanged.

## 6. Next verification

After the operator restores an API balance or supplies a reachable local model, rerun Ask on the preserved diagnostic project. Verify a nonempty answer, evidence markers, stored claims and provenance; inspect the content against the saved reading note. X.229 and X.231 are separate repairs. Further stage progression requires actual human gate decisions. No full research lifecycle, successful manuscript export or publication-ready result is certified by this diagnostic.

Suggested documentation commit message, not executed: `X.227: record live localhost workflow results and runtime blockers`.
