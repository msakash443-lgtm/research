# traceability.md — Spec → Plan → Code → Tests

> Maps every requirement in [research-automation-spec.md](research-automation-spec.md) to the
> [plan.md](../plan.md) tasks that deliver it, where it shows up (screen or endpoint), and the tests
> that prove it. **plan.md is the source of truth for status**; this table is a snapshot and must be
> updated whenever a task is added, finished or dropped (plan.md rule 31).
>
> Snapshot: 2026-10-03 · test baseline 184 passed · tests live in `research-main/research/backend/tests/`.

**Status key:** `[x]` done · `[?]` built, awaiting review · `[~]` in progress · `[ ]` not started ·
`—` no task (see **Gaps**). A row is only "done" when every listed task is `[x]` **and** a test exists.

---

## 1. Module requirements (spec §5)

### §5.1 Idea capture and scoping
| Spec | Requirement | Tasks | Screen / endpoint | Tests | Status |
|---|---|---|---|---|---|
| 5.1.1 | Cluster/de-dup ideas; extract constructs & contexts | M4.7 | — | — | `[ ]` |
| 5.1.2 | Rapid landscape scan | M4.7 | — | — | `[ ]` |
| 5.1.3 | Novelty heuristic **with visible comparators**; feasibility checklist | M4.7 | — | — | `[ ]` |
| 5.1 out | One-page idea brief | M4.7 | — | — | `[ ]` |
| 5.1 gate | G1 approve topic direction | M4.7, M0.5.2–M0.5.4 | — | — | `[ ]` |

### §5.2 Systematic literature search
| Spec | Requirement | Tasks | Screen / endpoint | Tests | Status |
|---|---|---|---|---|---|
| 5.2 in | Inclusion/exclusion criteria (PICO etc.) | M1.5.4 | — | — | `[ ]` |
| 5.2.1 | Boolean query builder, per-DB syntax | M1.5.1–M1.5.3 | — | — | `[ ]` |
| 5.2.2 | Multi-source retrieval | M1.3, M1.4.1–M1.4.7 | — | — | `[ ]` |
| 5.2.2 | Web search / crawl / PDF via ARC (opt-in) | M1.1.1–M1.1.11 | `POST /api/projects/{id}/research-runs` (`use_web_retrieval`) | `test_arc_retrieval.py` | `[?]` (8–11 open) |
| 5.2.2 | De-dup by DOI → title+year+first author | M0.2.2, M1.6.1–M1.6.3 | `POST .../sources` (DOI normalised) | `test_doi.py` | M0.2.2 `[?]`, M1.6 `[ ]` |
| 5.2.3 | Snowballing N rounds | M1.9.1–M1.9.2 | — | — | `[ ]` |
| 5.2.4 | Saved-query alerts | M1.12 | — | — | `[ ]` |
| 5.2.5 | Search log + PRISMA counts | M1.7.1–M1.7.3 | — | — | `[ ]` |
| 5.2 gate | G2: approve strategy before bulk retrieval; known-item test | M1.8.1–M1.8.2 | — | — | `[ ]` |
| 5.2 QC | Gold-set recall, report missed papers | M1.8.3, **X.15** | — | — | `[ ]` |

### §5.3 Screening
| Spec | Requirement | Tasks | Screen / endpoint | Tests | Status |
|---|---|---|---|---|---|
| 5.3 data | `ScreeningDecision`, reason codes, queue/decide/undo | M2.1.1–M2.1.3 | — | — | `[ ]` |
| 5.3.1 | AI pre-screen with rationale + confidence | M2.2.1–M2.2.3 | — | — | `[ ]` |
| 5.3.2 | Active-learning prioritisation | M2.6 | — | — | `[ ]` |
| 5.3.3 | Dual screen, kappa, conflict queue | M2.4.1–M2.4.2 | — | — | `[ ]` |
| 5.3.4 | OA full-text fetch; paywalled → manual upload | M2.7.1–M2.7.4 | — | — | `[ ]` |
| 5.3 UI | Keyboard-first screening queue | M2.3.1–M2.3.2, **X.14** (wireframe first) | — | — | `[ ]` |
| 5.3 gate | No AI auto-exclude without sampling audit; G3 | M2.5.1, M2.8.1 | — | — | `[ ]` |
| 5.3 QC | Stop AI pre-screen when agreement drops | M2.5.2, M2.9 | — | — | `[ ]` |

### §5.4 Extraction and evidence tables
| Spec | Requirement | Tasks | Screen / endpoint | Tests | Status |
|---|---|---|---|---|---|
| 5.4.1 | Versioned discipline schemas | M3.1.1–M3.1.2 | — | — | `[ ]` |
| 5.4.2 | LLM extraction with evidence spans | M3.2.1, M3.3.1–M3.3.2 | — | — | `[ ]` |
| 5.4.3 | Verbatim span verification | M3.4.1–M3.4.2; groundwork M0.2.6 (immutable excerpts) | — | `test_excerpt_immutability.py` | M0.2.6 `[?]`, M3.4 `[ ]` |
| 5.4.4 | Matrix view, every cell links to source; CSV/Excel | M3.5.1–M3.5.2, **X.14** | — | — | `[ ]` |
| 5.4 gate | G4 verify critical fields, side-by-side PDF | M3.6.1–M3.6.2 | — | — | `[ ]` |
| 5.4 QC | Double extraction on a sample | M3.3.3 | — | — | `[ ]` |

### §5.5 Synthesis, constructs and gaps
| Spec | Requirement | Tasks | Screen / endpoint | Tests | Status |
|---|---|---|---|---|---|
| 5.5.1 | Thematic clustering, editable labels | M3.8.1 | — | — | `[ ]` |
| 5.5.2–3 | Construct inventory (jingle-jangle); theory map | M3.7.1 | — | — | `[ ]` |
| 5.5.4 | Method/context matrices | M3.8.2 | — | — | `[ ]` |
| 5.5.5 | Gap miner, ≥ 2 supporting papers | M3.9.1–M3.9.2 | — | — | `[ ]` |
| 5.5.6 | Bibliometric views/exports | M3.12 | — | — | `[ ]` |
| 5.5 gate | G5 curate gaps | M3.9.3 | — | — | `[ ]` |
| 5.5 QC | Contradiction detection | M3.10.1 | — | — | `[ ]` |
| §4 inv. | Claim with no evidence can't be `verified` | M3.11.1–M3.11.3 | — | — | `[ ]` |

### §5.6 RQs, hypotheses, framework
| Spec | Requirement | Tasks | Screen / endpoint | Tests | Status |
|---|---|---|---|---|---|
| 5.6.1 | RQ/hypothesis drafting + FINER critique | M4.1 | — | — | `[ ]` |
| 5.6.2–4 | Conceptual model, diagram, construct–measure check | M4.2 | — | — | `[ ]` |
| 5.6 gate | G6 approve RQs, hypotheses, model | M4.8 *(new)* | — | — | `[ ]` |

### §5.7 Design and ethics
| Spec | Requirement | Tasks | Screen / endpoint | Tests | Status |
|---|---|---|---|---|---|
| 5.7.1–2 | Method recommender; sampling/power | M4.3 | — | — | `[ ]` |
| 5.7.3 | Instrument builder; item-quality checks | M4.4 | — | — | `[ ]` |
| 5.7.4 | Ethics pack; G7 sign-off | M4.5 | — | — | `[ ]` |
| 5.7.5 | Pre-registration draft | M4.6 | — | — | `[ ]` |

### §5.8 Data collection and management
| Spec | Requirement | Tasks | Screen / endpoint | Tests | Status |
|---|---|---|---|---|---|
| 5.8.1–2 | Survey deploy/ingest; response-quality monitoring | M5.2, M5.1 | — | — | `[ ]` |
| 5.8.3 | Logged reversible cleaning; immutable raw data | M5.1 | — | — | `[ ]` |
| 5.8.4 | Anonymisation; **codebook auto-generation** | M5.6, M5.7 *(new)* | — | — | `[ ]` |
| 5.8.5 | Qualitative **transcription**, anonymisation, storage | M5.7 *(new)* | — | — | `[ ]` |
| 5.8 gate | **Approve exclusion rules before applying**; never silently drop cases | M5.7 *(new)* | — | — | `[ ]` |

### §5.9 Analysis
| Spec | Requirement | Tasks | Screen / endpoint | Tests | Status |
|---|---|---|---|---|---|
| 5.9 Q1–2 | Scripts from plan; pinned env, seeds; G8 | M5.3 | — | — | `[ ]` |
| 5.9 Q3 | Robustness suite | M5.4 | — | — | `[ ]` |
| 5.9 Q4 | **Journal-style tables/figures**; notebooks | M5.4, M5.8 *(new)* | — | — | `[ ]` |
| 5.9 Qual | Coding assist, codebook, inter-coder agreement | M5.5 | — | — | `[ ]` |
| 5.9 gate | G9; **pre-registration deviations documented** | M5.8 *(new)*, M6.9 *(new)* | — | — | `[ ]` |
| 5.9 QC | Re-run gives identical numbers | M5.4 | — | — | `[ ]` |

### §5.10 Interpretation and discussion
| Spec | Requirement | Tasks | Screen / endpoint | Tests | Status |
|---|---|---|---|---|---|
| 5.10.1–2 | **Findings-to-literature map; discussion draft with evidence links** | M6.9 *(new)* | — | — | `[ ]` |
| 5.10.3 | Overclaiming check | M6.7 | — | — | `[ ]` |
| 5.10 gate | G9 approve interpretation / contribution | M6.9 *(new)* | — | — | `[ ]` |

### §5.11 Writing and manuscript assembly
| Spec | Requirement | Tasks | Screen / endpoint | Tests | Status |
|---|---|---|---|---|---|
| 5.11.1 | Argument outline → section drafts | M6.1 | — | — | `[ ]` |
| 5.11.2 | **Genre templates** (IMRaD, conceptual, review, thesis) | M6.10 *(new)* | — | — | `[ ]` |
| 5.11.3 | Claim–evidence linker; verified refs only | M6.2, M1.10, **X.14** | — | — | `[ ]` |
| 5.11.4 | Style help with diffs | M6.8 | — | — | `[ ]` |
| 5.11.5 | Ref-manager sync; CSL | M1.11, M6.5 | — | — | `[ ]` |
| 5.11.6 | **Abstract, keywords, highlights, cover letter**; statements | M6.10 *(new)*, M6.5, M6.6 | — | — | `[ ]` |
| 5.11.7 | Similarity pre-check | M6.7 | — | — | `[ ]` |
| 5.11 gate | **G10 approve each section + AI-involvement level** | M6.1, M6.11 *(new)* | — | — | `[ ]` |
| 5.11 QC | Zero unresolved citations / orphan claims | M6.2, M6.4 | — | — | `[ ]` |

### §5.12 Revision
| Spec | Requirement | Tasks | Screen / endpoint | Tests | Status |
|---|---|---|---|---|---|
| 5.12.1 | Simulated reviewer personas | M7.1 | — | — | `[ ]` |
| 5.12.2 | Consistency checker | M7.2 | — | — | `[ ]` |
| 5.12.3 | Real-review issue tracker, response letter, redline | M7.3 | — | — | `[ ]` |

### §5.13 Venue, submission and tracking
| Spec | Requirement | Tasks | Screen / endpoint | Tests | Status |
|---|---|---|---|---|---|
| 5.13.1–2 | Journal matcher; predatory screening | M7.4 | — | — | `[ ]` |
| 5.13.3 | **Format to author guidelines** | M7.6 *(new)* | — | — | `[ ]` |
| 5.13.4 | Submission tracker, reminders, calendar | M7.5 | — | — | `[ ]` |
| 5.13.5 | Post-acceptance (ORCID, preprint, Zenodo/OSF); **CFP alerts** | M7.5, M7.6 *(new)* | — | — | `[ ]` |
| 5.13 gate | G11 final sign-off | M7.5 | — | — | `[ ]` |

---

## 2. Cross-cutting requirements

| Spec | Requirement | Tasks | Screen / endpoint | Tests | Status |
|---|---|---|---|---|---|
| §3.2 | Orchestrator: durable queue, pause, retry, re-entry | M0.2.1, M0.6.1–M0.6.5, M0.6.10, X.1.1 | worker | `test_worker_reaper.py`, `test_task_queue.py`, `test_task_queue_postgres.py` (real PostgreSQL: double claim, backoff, lease expiry) | reviewed 2026-10-05: most `[x]`; M0.6.4 `[?]` (defect → M0.6.10); X.1.1 `[?]` |
| §3.2 | Object store for PDFs | M0.10.2 | — | — | `[ ]` |
| §3.3 | Citation verifier | M1.10.1–M1.10.5; human verify M0.2.4 | `POST .../sources/{id}/verify`, `POST .../sources/{id}/check` | `test_source_verify.py`, `test_citation_verifier.py`, `test_source_verification.py`, `test_citation_guard.py` | M0.2.4, M1.10.1–.2 `[x]`; M1.10.3–.4 `[?]`; M1.10.5 `[ ]` |
| §3.3 | Provenance logger (append-only) | M0.4.1–M0.4.5 | `GET .../audit`, `.../audit/export` | `test_audit_event_model.py`, `test_audit_wiring.py`, `test_audit_append_only.py`, `test_audit_api.py`, `test_created_by.py` | `[?]` |
| §3.3 | Config service (discipline profile) | M0.7.1–M0.7.2 | — | — | `[ ]` |
| §3.3 | Cost / rate limiter | M0.9.1–M0.9.2 | — | — | `[ ]` |
| §4 | Paper model fields | M0.10.1 | — | — | `[ ]` |
| §6 | Connector interface, caching, backoff, ToS flags | M1.3.1–M1.3.4 | — | — | `[ ]` |
| §6 | **ORCID/ROR author disambiguation** | X.18 *(new)* | — | — | `[ ]` |
| §7.1 | Retrieval-grounded; untrusted-text fencing | M1.2.1–M1.2.4, M3.3.1 | — | — | `[ ]` |
| §7.2 | JSON-schema outputs, reject + retry | M0.8.2, M0.8.6 | — | `test_llm_success_path.py` (fail-loudly paths) | M0.2.5 `[?]`, M0.8.2 `[?]`, M0.8.6 `[ ]` |
| §7.3 | Citation guard (hard block) | M1.10.3, M1.10.5, M1.13.1, M6.4 | research runs (`research_run.citation_rejected`); source cards: verify / check buttons | `test_citation_guard.py` | M1.10.3–.5, M1.13.1 `[?]`; human sign-off X.30 `[!]`; claims/export wait for M3.11/M6.4 |
| §7.4 | Numbers from code | M6.3 | — | — | `[ ]` |
| §7.5 | Confidence + abstention | M0.8.3, M0.8.6 | — | — | `[ ]` |
| §7.6 | Versioned prompts; log `prompt_version` + `model_id` | M0.8.1, M0.2.3 | `GET .../research-runs` (`provider_model`, `input_snapshot`) | `test_run_read_fields.py` | M0.2.3 `[?]`, M0.8.1 `[ ]` |
| §7.7 | Evaluation harness / gold datasets | X.5, **X.15** | — | — | `[ ]` |
| §7.8 | Bias & coverage warnings | X.6 | — | — | `[ ]` |
| §7.9 | Data privacy / local model | M0.8.4, M5.6 | — | — | `[ ]` |
| App. C | Stage state machine | M0.5.1, M0.5.7 | `stage` on project responses | `test_project_stage.py` | M0.5.1 `[?]` (stored only; no transitions yet) |

### §8 Gates (each must **block** the next step until a human approves)
| Gate | Stage | Gate task | Enforcement | Tests | Status |
|---|---|---|---|---|---|
| G1 | Idea scoping | M4.7 | M0.5.2–M0.5.4 | — | `[ ]` |
| G2 | Before bulk search | M1.8.1 | ″ | — | `[ ]` |
| G3 | After screening | M2.8.1 | ″ | — | `[ ]` |
| G4 | After extraction | M3.6.2 | ″ | — | `[ ]` |
| G5 | After gap analysis | M3.9.3 | ″ | — | `[ ]` |
| G6 | After RQ/framework | M4.8 *(new)* | ″ | — | `[ ]` |
| G7 | Before data collection | M4.5 | ″ | — | `[ ]` |
| G8 | Before analysis | M5.3 | ″ | — | `[ ]` |
| G9 | After analysis | M6.9 *(new)* | ″ | — | `[ ]` |
| G10 | Per manuscript section | M6.11 *(new)* | ″ | — | `[ ]` |
| G11 | Before submission | M7.5 | ″ | — | `[ ]` |
| all | Who may approve (role matrix) | M0.3.2, M0.3.4 | `project_access` role sets | `test_project_access.py`, `test_role_matrix.py` | `[?]` |

### §9 Integrity and ethics
| Requirement | Tasks | Tests | Status |
|---|---|---|---|
| No fabrication (hard to do accidentally) | M1.10, M3.4, M3.11.2, M6.2–M6.4 | `test_llm_success_path.py` (no placeholder output) | `[ ]` |
| AI-use disclosure from provenance log | M6.6 | — | `[ ]` |
| Authorship / CRediT; AI never an author | M6.6 | — | `[ ]` |
| Copyright: store full text only where licensed | M2.7.1, M1.3.4 | — | `[ ]` |
| Participant protection (consent, anonymisation, retention) | M4.5, M5.6, X.4 | — | `[ ]` |
| Reporting-guideline checklists | X.11 | — | `[ ]` |
| One-click reproducibility package | X.8 | — | `[ ]` |

### §10 Non-functional
| Area | Tasks | Tests | Status |
|---|---|---|---|
| Reliability (idempotent, resumable, no loss) | M0.2.1, M0.6.2, M0.6.4 | `test_worker_reaper.py` | `[?]` / `[ ]` |
| Security (roles, encryption, secrets) | M0.3.1–M0.3.5, X.4; existing sign-in (`/api/auth/*`) | `test_auth.py`, `test_project_access.py`, `test_role_matrix.py`, `test_project_members*.py`, `test_dashboard.py` (body limit) | `[?]` / `[ ]` |
| Auditability (immutable, exportable) | M0.4.1–M0.4.5 | `test_audit_*.py` | `[?]` |
| **Performance** (≥ 1,000 abstracts/h; 500 papers indexed < 1 h) | X.19 *(new)* | — | `[ ]` |
| Scalability (multi-user, queue workers) | M0.3, M0.6 | — | `[ ]` |
| Usability (keyboard screening, side-by-side, diffs) | M2.3.1, M3.6.1, M6.8, **X.14**, X.32 | — | `[ ]` |
| Portability (open export formats) | X.8, M1.11, M3.5.2, M6.5 | — | `[ ]` |
| Observability | X.7, M0.9.1 | — | `[ ]` |

### §11 Metrics
| Metric (target) | Tasks | Status |
|---|---|---|
| Search recall ≥ 95% on gold set | M1.8.3, X.15 | `[ ]` |
| Screening κ ≥ 0.8; false exclusion < 2% | M2.4.2, M2.9, X.15 | `[ ]` |
| Extraction ≥ 90% on critical fields | X.5, X.15 | `[ ]` |
| Citation verification 100% before export | M1.10, M6.4 | `[ ]` |
| Analysis re-run diff = 0 | M5.4 | `[ ]` |
| **Scholar outcomes** (time saved, corrections per 100 AI outputs, readiness) | X.20 *(new)* | `[ ]` |

### §13 Acceptance criteria (project definition of done)
| # | Criterion | Tasks | Status |
|---|---|---|---|
| 13.1 | Idea → verified evidence table + ranked gaps, full search log, PRISMA | M3.13.1, X.16 | `[ ]` |
| 13.2 | Every citation resolves; unverifiable can't be exported | M1.10.3, M6.4 | `[ ]` (research-run answers already blocked, M1.10.3 `[?]`; export not built) |
| 13.3 | Every number traceable to an analysis run | M6.3, M5.3 | `[ ]` |
| 13.4 | Every AI artifact shows who/what/when/model + approval | M0.4, M0.8.1, **X.17** | `[ ]` (data `[?]`, UI `[ ]`) |
| 13.5 | Re-enter any stage; downstream flagged stale | M0.5.5 | `[ ]` |
| 13.6 | One-action audit/replication package | X.8 | `[ ]` |
| all | Automated acceptance suite | X.10 | `[ ]` |

---

## 3. Non-negotiables checked at every demo

| # | Check | Delivered by | Today |
|---|---|---|---|
| N1 | Every citation clicks through to a verified source; unverifiable is **blocked**, not warned | M1.10.3, M1.13, M3.5.1, M6.4 | Partly built (2026-10-05): a research-run answer citing an unverified source is blocked and audited (M1.10.3 `[?]`); no click-through UI yet (M1.13). |
| N2 | Every AI output shows producer, model, date, approval status | M0.4, M0.8.1, X.17 | Data partly recorded (`created_by`, `provider_model`, audit events); nothing shown in UI; `prompt_version` empty. |
| N3 | The 11 gates block the next step until approved | M0.5.2–M0.5.4 + per-gate tasks in §8 table | Not built (only the stage field exists, M0.5.1 `[?]`). |
| N4 | Changing an earlier stage marks downstream work stale | M0.5.5 | Not built. |
| N5 | Every number in a draft links to an analysis run | M6.3 | Not built (M6). |

---

## 4. Gaps (spec lines with no task before this table) → new plan.md tasks

| New task | Covers |
|---|---|
| M4.8 | G6 approval gate for RQs/hypotheses/model (§5.6, §8) |
| M5.7 | §5.8: exclusion-rule approval before applying, codebook auto-generation, qualitative transcription |
| M5.8 | §5.9: journal-style tables/figures; logging deviations from pre-registration |
| M6.9 | §5.10 interpretation module: findings-to-literature map, discussion draft, G9 |
| M6.10 | §5.11.2 genre templates; §5.11.6 abstract/keywords/highlights/cover letter |
| M6.11 | G10 per-section approval gate (§5.11, §8) |
| M7.6 | §5.13.3 format to author guidelines; §5.13.5 CFP/conference alerts |
| X.17 | §13.4 UI provenance display (who/what/model/date/approval) |
| X.18 | §6 ORCID/ROR author disambiguation |
| X.19 | §10 performance targets: benchmark + regression check |
| X.20 | §11 scholar-outcome metrics |

## 5. Work with no spec line (scope check)

| Task / code | Justification |
|---|---|
| M0.1 workspace hygiene, M0.1.6 `.env.example`, M0.1.7 venv docs | Engineering hygiene; no user-facing scope. |
| ~~M1.1 ARC web search/crawl/PDF microservice~~ | Resolved 2026-10-03 (Q11): kept, and added to spec §6, so it now maps to §5.2.2. |
| M1.1.11–M1.1.12 ARC hardening; X.9 compose profile | Only needed because of M1.1. |
| X.1 CI (X.1.1 Postgres, X.1.2 ARC), X.2–X.3 docs, X.26 `.env.example` legacy settings, X.28 connector settings in DEPLOYMENT.md, X.30 human sign-off | Engineering hygiene / review process. |
| X.31 researcher workspace (capture, inbox, highlights, editor, offline sync) | From the master-requirements doc §3.2 (2026-10-05), not the spec; closest spec lines §5.1 (idea capture, M4.7) and §5.11 (writing). |
| Existing dashboard + request body limit (`test_dashboard.py`) | Pre-plan app feature; §10 Security. |
| X.25 UI follow-ups (double submit, drawer focus, asset caching, nits; X.25.4 dropped; X.25.7 context cap) | Found in review of X.22–X.24; usability/robustness of existing UI. |
| X.24 project/context form submit bug | Defect fix in existing UI (found during X.23). |
| X.23 UI order fixes (source dialog, section order, phone jump links, context order) | User request 2026-10-03; usability of the existing project page (closest: §10 Usability). |
| X.22 responsive web + mobile layouts | User request 2026-10-03; usability of existing screens on phones/tablets (closest: §10 Usability). |
| X.32 three-pane step workspace UI + idea quick-capture (`ContextKind.idea`) | User request 2026-10-05; wires the already-built M0/M1 endpoints (criteria, seeds, searches, connectors, PRISMA, profile, members, audit, source verify/check/merge, stage re-entry) into real screens, covering M0.3.5/M0.5.6/M1.3.6/M1.13.1's UI subtasks; idea capture is project-scoped memory, not spec §5.1's AI clustering (that stays M4.7). Rule-32 wireframe waived by the scholar (Decisions log 2026-10-05). |
| B.1, B.2 backlog | Unscheduled; B.2 waits on Q5. |
| X.27 review backlog | Found via the master-requirements-doc audit (2026-10-05); clears the growing `[?]` second-review backlog — process gap, not a spec requirement. |
