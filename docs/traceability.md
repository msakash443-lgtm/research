# traceability.md — Spec → Plan → Code → Tests

> Maps every requirement in [research-automation-spec.md](research-automation-spec.md) to the
> [plan.md](../plan.md) tasks that deliver it, where it shows up (screen or endpoint), and the tests
> that prove it. **plan.md is the source of truth for status**; this table is a snapshot and must be
> updated whenever a task is added, finished or dropped (plan.md rule 31).
>
> Snapshot: 2026-10-19 (M3 implementation refreshed) · test baseline **3073 passed, 13 skipped, 0 failed** (full `pytest -q`, SQLite/offline) · tests live in `backend/tests/`. M0/M1/M2/M4 core exits are recorded; M3 implementation is verified, but X.15/M3.15/X.16 real-data exit evidence remains blocked.
> Status here follows plan.md as of the snapshot date; where the two disagree, plan.md wins.

**Status key:** `[x]` done · `[?]` built, awaiting review · `[~]` in progress · `[ ]` not started ·
`—` no task (see **Gaps**). A row is only "done" when every listed task is `[x]` **and** a test exists.

---

## 1. Module requirements (spec §5)

### §5.1 Idea capture and scoping
| Spec | Requirement | Tasks | Screen / endpoint | Tests | Status |
|---|---|---|---|---|---|
| 5.1.1 | Cluster/de-dup ideas; extract constructs & contexts | M4.7.c | `GET /api/projects/{id}/idea-brief` (`clusters`, `constructs`, `duplicate_ideas`) | `test_idea_clustering.py`, `test_idea_brief.py` | `[?]` (lexical only; contexts not extracted) |
| 5.1.2 | Rapid landscape scan | M4.7.a, M4.7.d | `GET /api/projects/{id}/idea-brief/landscape-scan?query=&limit=` | `test_landscape_scan.py`, live OpenAlex connector check | `[?]` (249 focused tests pass; direct live OpenAlex check passed, but route-level live evidence and globally top-cited ordering remain open; output is top-cited in returned sample) |
| 5.1.3 | Novelty heuristic **with visible comparators**; feasibility checklist | M4.7, M4.7.b | `GET/PUT /api/projects/{id}/idea-brief`; `GET …/idea-brief/semantic-novelty` (M4.7.b: embedding cosine, per-request, nothing stored, `model_id` recorded) | `test_idea_scoping.py`, `test_idea_brief.py`, `test_semantic_novelty.py`, `test_semantic_novelty_api.py`, `test_role_matrix.py` | `[?]` (lexical and semantic comparators built; semantic one needs EMBEDDING_MODEL and independent review) |
| 5.1 in | Free-text idea/voice-note capture, scoping notes (design: `docs/researcher-workspace-design.md`) | X.31.1–X.31.2, X.31.17 | `POST/GET/PATCH /api/notes`, capture dialog; triage: `POST /api/notes/{id}/file`, `…/promote`, `GET …/suggestions`, `GET /api/projects/{id}/context?kind=`; inbox and decision-log screen (`#/inbox`, `web/js/inbox.js`) | `test_notes.py`, `test_note_guard.py`, `test_notes_triage.py`, `test_note_suggest.py`, `web/js/test/inboxLogic.test.mjs` | X.31.1 `[?]` (quick capture built); X.31.2.a `[?]` (triage backend); X.31.2.1 `[?]` (inbox and decision-log screen built ahead of the wireframe walkthrough, rule 32 override); X.31.17 `[ ]` |
| 5.1 out | One-page idea brief | M4.7, M4.7.a | `GET/PUT /api/projects/{id}/idea-brief` and landscape scan | `test_idea_brief.py`, `test_landscape_scan.py` | `[?]` (brief built; landscape snapshot remains pending M4.7.a/d closure) |
| 5.1 gate | G1 approve topic direction | M4.7, M0.5.2–M0.5.4 | `ready_for_g1` on the idea-brief GET (readiness only; approval stays in the gates list) | `test_idea_brief.py` | `[ ]` (readiness built; G1 approval unchanged) |

### §5.2 Systematic literature search
| Spec | Requirement | Tasks | Screen / endpoint | Tests | Status |
|---|---|---|---|---|---|
| 5.2 in | Inclusion/exclusion criteria (PICO etc.) | M1.5.4 | criteria endpoints | `test_criteria.py` | `[x]` |
| 5.2.1 | Boolean query builder, per-DB syntax | M1.5.1–M1.5.6 | search-query endpoints | `test_search_query*.py` | `[x]` (M1.5 closed 2026-10-10) |
| 5.2.2 | Multi-source retrieval | M1.3, M1.4.1–M1.4.8 | connectors (OpenAlex, Crossref, Semantic Scholar, arXiv, Unpaywall, PubMed/Europe PMC, CORE, OpenCitations, Scopus, Web of Science, IEEE Xplore); `GET` connector list | `test_connector_*.py`, `test_*_connector.py`, `test_arxiv_fulltext.py`, `test_scopus_connector.py`, `test_web_of_science_connector.py`, `test_ieee_xplore_connector.py`, `test_search_runner.py` | `[x]` (M1.3 incl. M1.3.8 cross-process breaker, and M1.4 both closed 2026-10-10) |
| 5.2.2 | Web search / crawl / PDF via ARC (opt-in) | M1.1.1–M1.1.14 | `POST /api/projects/{id}/research-runs` (`use_web_retrieval`) | `test_arc_retrieval.py`, `test_arc_fencing.py` | `[x]` (M1.1 closed 2026-10-10, all 14 subtasks incl. M1.1.13) |
| 5.2.2 | De-dup by DOI → title+year+first author | M0.2.2, M1.6.1–M1.6.3 | `POST .../sources` (DOI normalised) | `test_doi.py`, `test_dedupe.py`, `test_merge.py`, `test_source_merge.py` | M0.2.2 and M1.6.1–.4 `[x]` |
| 5.2.3 | Snowballing N rounds | M1.9.1–M1.9.2 | `POST/GET /api/projects/{id}/snowball` (G2-gated task `snowball_run`); `found_via` on sources + screening queue; PRISMA `other_methods.citation_searching` | `test_snowball.py` | `[x]` (M1.9 closed 2026-10-10) |
| 5.2.4 | Saved-query alerts | M1.12 | `POST …/searches/{id}/alerts` (subscribe, 1 h–30 d interval), `GET …/alerts` (last-run state + new results), `POST …/alerts/{id}/run` (immediate), `DELETE …/alerts/{id}`; G2-gated task `query_alert`, worker sweep per due slot | `test_query_alerts.py` | M1.12 `[x]` (built + closed at user request 2026-10-10; v1: no email/calendar, results not saved as sources) |
| 5.2.5 | Search log + PRISMA counts | M1.7.1–M1.7.3 | search runs, `GET …/prisma` | `test_search_runner.py`, `test_prisma.py` | `[x]` (counts for screening stages wait on M2) |
| 5.2 gate | G2: approve strategy before bulk retrieval; known-item test | M1.8.1–M1.8.2 | G2 gate; `GET …/known-items` | `test_search_gate.py`, `test_known_items.py` | `[x]` |
| 5.2 QC | Gold-set recall, report missed papers | M1.8.3–M1.8.4, **X.15** | — | `test_gold_set.py` (loader only), `test_recall_report.py` | M1.8.3/M1.8.4 `[x]` (M1.8 closed 2026-10-10; exact-fraction rounding fix reviewed); **real recall number not measured** — X.15 `[!]` blocked on Q10 |

### §5.3 Screening
| Spec | Requirement | Tasks | Screen / endpoint | Tests | Status |
|---|---|---|---|---|---|
| 5.3 data | `ScreeningDecision`, reason codes, queue/decide/undo | M2.1.1–M2.1.3 | screening queue / decide / undo endpoints | `test_screening.py` | `[x]` |
| 5.3.1 | AI pre-screen with rationale + confidence | M2.2.1–M2.2.3 | `POST …/screening/prescreen`, task `screening_prescreen` | `test_prescreen.py` | `[x]` (not tried against a real model) |
| 5.3.2 | Active-learning prioritisation | M2.6 | `GET/PUT …/screening/active-learning`, `screening/queue?sort=relevance` | `test_active_learning.py`, `test_screening.py`, `test_role_matrix.py` | `[x]` (naive-Bayes bag-of-words classifier, no embeddings; falls back to oldest-first with no training signal) |
| 5.3.3 | Dual screen, kappa, conflict queue | M2.4.1–M2.4.2b | `GET/PUT …/screening/dual-screen`, `screening/queue` + `screening/decisions` (blinded), `screening/decisions/undo` (per-reviewer), `GET …/screening/agreement` | `test_screening.py`, `test_role_matrix.py`, `test_agreement.py` | `[x]` |
| 5.3.4 | OA full-text fetch; paywalled → manual upload; full-text screening stage | M2.7.1–M2.7.4, M2.7.7 | `POST …/sources/{id}/fulltext/fetch`, `POST …/sources/{id}/fulltext/upload`, `GET …/sources/{id}/fulltext`, `screening/queue?stage=full_text` | `test_oa_fetch.py`, `test_fulltext_upload.py`, `test_screening.py`, `test_role_matrix.py` | `[x]` (M2.7.5 DNS-rebinding pin and M2.7.6 more OA sources still `[ ]`) |
| 5.3 UI | Keyboard-first screening queue | M2.3.1, X.14, X.32 | authenticated workspace/screening UI; wireframe `docs/wireframes/screening-queue.html` | `test_screening.py`, frontend suite (84), authenticated browser smoke | `[x]` (core UI verified; M2.3.2/M2.3.3 policy-dependent bulk/conflict extensions remain deferred) |
| 5.3 gate | No AI auto-exclude without sampling audit; G3 | M2.5.1, M2.8.1 | `GET/PUT …/screening/auto-exclude`, `GET …/screening/auto-exclude/audits`, `POST …/auto-exclude/audits/{id}/review` | `test_prescreen.py`, `test_screening.py`, `test_ai_quality.py`, `test_role_matrix.py` | `[x]` (sampling audit atomicity, G3 lock and reason guard verified) |
| 5.3 QC | Stop AI pre-screen when agreement drops | M2.5.2 | `GET …/screening/ai-agreement`, `PUT …/screening/ai-prescreen` | `test_prescreen.py`, `test_screening.py`, `test_ai_quality.py` | `[x]` (M2.9 gold-set measurement deferred on X.15; guard behavior verified) |

### §5.4 Extraction and evidence tables
| Spec | Requirement | Tasks | Screen / endpoint | Tests | Status |
|---|---|---|---|---|---|
| 5.4.1 | Versioned discipline schemas with nested Appendix B value validation | M3.1.1–M3.1.2 | `extraction_schema.py`, `extraction_schemas/` | `test_extraction_schema.py`, `test_project_schemas.py`, `test_project_schema_guard.py` | M3.1 `[x]` (independent review found no defect) |
| 5.4.2 | LLM extraction with evidence spans | M3.2.1, M3.3.1–M3.3.2 | `ai_extraction.py`, `routers/extractions.py`, `evidence_extraction.v1.toml` | `test_ai_extraction.py`, `test_extractions.py`, `test_role_matrix.py` | M3.2 `[x]`; M3.3 `[x]` (independently reviewed; post-fix full suite: 2186 passed, 6 skipped) |
| 5.4.3 | Verbatim span verification | M3.4.1–M3.4.2; groundwork M0.2.6 (immutable excerpts) | shared span checker and extraction save/verify paths | `test_extraction_verification.py`, `test_excerpt_immutability.py` | M3.4 `[x]` implementation verified; real-paper demo pending X.15/X.16 |
| 5.4.4 | Matrix view, every cell links to source; CSV/Excel | M3.5.1–M3.5.2, X.14 | evidence matrix/export endpoints; evidence-table wireframe | `test_evidence_matrix.py`, role tests | M3.5 `[x]` implementation verified; real-paper UI/demo pending X.14.2/X.16 |
| 5.4 gate | G4 verify critical fields, side-by-side PDF | M3.6.1–M3.6.2 | extraction verification and G4 gate enforcement | `test_extraction_verification.py`, gate/role tests | M3.6 `[x]` implementation verified; actual G4 approval remains human/data-dependent |
| 5.4 QC | Double extraction on a sample | M3.3.3 | `ai_extraction.py` double-extraction path | `test_ai_extraction.py` | `[x]` implementation verified; real sample agreement awaits X.15 |

### §5.5 Synthesis, constructs and gaps
| Spec | Requirement | Tasks | Screen / endpoint | Tests | Status |
|---|---|---|---|---|---|
| 5.5.1 | Thematic clustering, editable labels | M3.8.1 | `app/thematic_clusters.py`, `routers/clusters.py` | `tests/test_thematic_clusters.py` | `[x]` (independently reviewed; API only; UI waits on a wireframe) |
| 5.5.2–3 | Construct inventory (jingle-jangle); theory map | M3.7.1 | `GET/POST /api/projects/{id}/constructs`; `GET/POST /api/projects/{id}/theories` | `test_constructs.py`, `test_role_matrix.py` | `[x]` (independently reviewed; project-scoped theory/construct links are database-enforced) |
| 5.5.4 | Method/context matrices | M3.8.2 | `app/coverage_matrix.py`, `routers/coverage.py` | `tests/test_coverage_matrix.py` | `[x]` (independently reviewed; API only; UI waits on a wireframe) |
| 5.5.5 | Gap miner, ≥ 2 supporting papers | M3.9.1–M3.9.2 | `GET/POST/PATCH /api/projects/{id}/gaps` | `test_gaps.py`, `test_role_matrix.py` | `[x]` (independently approved; evidence and future-research provenance are project-scoped) |
| 5.5.6 | Bibliometric views/exports | M3.12, M3.12.1 | `GET/PUT /api/projects/{id}/bibliometrics/inputs/{source_id}`; `GET …/networks`, `…/publication-trends`, `…/export/vosviewer/{kind}.zip`, `…/export/citespace.csv` | `test_bibliometric_networks.py`, `test_bibliometrics.py`, `test_role_matrix.py` | `[?]` (persisted project-scoped source inputs; independent re-review and a green full suite pending) |
| 5.5 gate | G5 curate gaps | M3.9.3 | Existing gate API plus gap curation route | `test_gaps.py`, `test_role_matrix.py` | `[ ]` (human approval required; never auto-approved) |
| 5.5 QC | Contradiction detection | M3.10.1 | `GET /api/projects/{id}/evidence-matrix/contradictions` | `test_contradictions.py`, `test_role_matrix.py` | `[x]` (independently reviewed; verified findings, explicit directional matches, and cell-level quote provenance) |
| §4 inv. | Claim with no evidence can't be `verified` | M3.11.1–M3.11.3 | — | — | `[ ]` |

### §5.6 RQs, hypotheses, framework
| Spec | Requirement | Tasks | Screen / endpoint | Tests | Status |
|---|---|---|---|---|---|
| 5.6.1 | Human-owned RQ/hypothesis drafts linked to selected gaps; transparent advisory FINER critique | M4.1 | `GET/POST/PATCH /api/projects/{id}/research-questions`; `GET …/research-questions/{id}/finer`; `GET/POST/PATCH /api/projects/{id}/hypotheses` | `test_research_questions.py`, `test_role_matrix.py` | `[x]` (focused 243 passed; full pytest 3072 passed, 13 skipped, 9 warnings) |
| 5.6.2–4 | Conceptual model, diagram, construct–measure check | M4.2, M4.2.1 | conceptual-model API | `test_conceptual_model.py`, role/lookup tests | `[x]` (independently accepted; API-only, no UI by design) |
| 5.6 gate | G6 approve RQs, hypotheses, model | M4.8 | gate/stage APIs; EC-8.2 checks linked RQs/hypotheses and selected gaps | `test_exit_conditions.py`, `test_gate_required_conditions.py`, `test_guidance.py` | `[x]` condition implemented and audited Recommended override path verified; human G6 approval remains a gate action |

### §5.7 Design and ethics
| Spec | Requirement | Tasks | Screen / endpoint | Tests | Status |
|---|---|---|---|---|---|
| 5.7.1–2 | Method recommender; sampling/power; qualitative saturation guidance | M4.3 `[x]`, M4.3.1 `[x]`, M4.3.2 `[x]` | `GET /api/projects/{id}/design/method-recommendation`; `GET …/design/sample-size`; `GET …/design/saturation-guidance` | `test_design_tools.py`, `test_saturation_guidance.py`, `test_role_matrix.py`, `test_never_lock.py` | `[x]` (accepted; SQLite/offline; cited saturation ranges remain advisory) |
| 5.7.3 | Instrument builder; item-quality checks | M4.4, M4.4.1–M4.4.3 | instrument endpoints | `test_item_checks.py`, `test_measure_library.py`, `test_validity_plan.py`, `test_role_matrix.py` | `[x]` (focused instrument suite 328 passed; static citation-only measure library, licences remain `to check`) |
| 5.7.4 | Ethics pack; G7 sign-off | M4.5 | `GET /api/projects/{id}/ethics-pack` | `test_ethics_pack.py`, gate/role tests | `[x]` pack owner-approved with bracketed institutional facts; actual G7 approval remains a human gate action |
| 5.7.5 | Pre-registration draft | M4.6 | `GET /api/projects/{id}/preregistration` | `test_preregistration.py`, role tests | `[x]` (owner approved as an advisory, non-submitting draft) |

### §5.8 Data collection and management
| Spec | Requirement | Tasks | Screen / endpoint | Tests | Status |
|---|---|---|---|---|---|
| 5.8.1–2 | Survey deploy/ingest; response-quality monitoring | M5.2, M5.1 | Google Forms import and rule-based quality endpoints | `test_survey_google_forms.py`, `test_response_quality.py`, `test_survey_api.py`, role tests | M5.2 `[?]` (pagination safety verified; live Google API/token refresh unverified) |
| 5.8.3 | Logged reversible cleaning; immutable raw data | M5.1 | dataset version/content/clean routes | `test_datasets.py`, `test_role_matrix.py` | `[x]` ingestion/cleaning verified; API-level immutability and Q4 participant-analysis limitation remain documented |
| 5.8.4 | Anonymisation; codebook auto-generation | M5.6, M5.7 | pseudonymisation/codebook endpoints | dataset/codebook/role tests | M5.6 `[?]` local-model participant path absent; M5.7.2 `[x]` codebook accepted |
| 5.8.5 | Qualitative transcription, anonymisation, storage | M5.7 | transcript anonymisation endpoint | transcript/role tests | M5.7.3 `[x]` anonymisation accepted; M5.7.4 `[!]` no approved speech-to-text service |
| 5.8 gate | Approve exclusion rules before applying; never silently drop cases | M5.7.1 | exclusion-rule propose/approve/apply endpoints | `test_exclusion_rules.py`, role tests | `[x]` approval/hash/dataset binding and no-silent-drop behavior verified |

### §5.9 Analysis
| Spec | Requirement | Tasks | Screen / endpoint | Tests | Status |
|---|---|---|---|---|---|
| 5.9 Q1–2 | Scripts from plan; pinned env, seeds; G8 | M5.3 | analysis-plan/script/run endpoints | analysis-plan/runner/gate tests | `[x]` M5.3 closed 2026-10-19: plan lock (M5.3.1), script generation (M5.3.2/.2a/.2b), sandboxed Docker runs (M5.3.3, live digest-pinned verification) and the G8 precondition (M5.3.4) all `[x]` |
| 5.9 Q3 | Robustness suite | M5.4 | pure robustness helpers + robustness-suite/notebook-export routes | `test_robustness.py`, `test_robustness_suite.py` | `[x]` M5.4.1 helpers and M5.4.2 suite (alternative specs, outlier/missing-data comparisons, re-run identity, notebook export) both built and independently reviewed |
| 5.9 Q4 | **Journal-style tables/figures**; notebooks | M5.4, M5.8 *(new)* | — | — | `[x]` M5.4.2 notebook export built; M5.8 (journal-style tables/figures) unblocked 2026-10-19 (M5.3 closed) but not yet started |
| 5.9 Qual | Coding assist, codebook, inter-coder agreement | M5.5 | pure coding state, codebook storage, plus optional local-model suggestion routes | qualitative coding/API tests | `[?]` M5.5.1 logic and M5.5.2 codebook storage both built, need independent re-review; AI-generated suggestions remain Q4/local-model constrained |
| 5.9 gate | G9; **pre-registration deviations documented** | M5.8 *(new)*, M6.9 *(new)* | — | — | `[ ]` |
| 5.9 QC | Re-run gives identical numbers | M5.4 | — | — | `[?]` fingerprint helper (M5.4.1) and re-run identity check (M5.4.2) built, needs review |

### §5.10 Interpretation and discussion
| Spec | Requirement | Tasks | Screen / endpoint | Tests | Status |
|---|---|---|---|---|---|
| 5.10.1–2 | **Findings-to-literature map; discussion draft with evidence links** | M6.9 *(new)* | — | — | `[ ]` |
| 5.10.3 | Overclaiming check | M6.7 | — | — | `[ ]` |
| 5.10 gate | G9 approve interpretation / contribution | M6.9 *(new)* | — | — | `[ ]` |

### §5.11 Writing and manuscript assembly
| Spec | Requirement | Tasks | Screen / endpoint | Tests | Status |
|---|---|---|---|---|---|
| 5.11.1 | Argument outline → section drafts | M6.1 `[?]` (claim→section binding, outline endpoint); drafting M6.1.a `[ ]` | `GET /api/projects/{id}/documents/{doc}/outline` | `test_claim_section.py` + role-matrix row | `[ ]` |
| 5.11.2 | **Genre templates** (IMRaD, conceptual, review, thesis) | M6.10 *(new)* | — | — | `[ ]` |
| 5.11.3 | Claim–evidence linker; verified refs only | M6.2 `[?]` (linker report), M1.10, **X.14**; see also X.31.9/X.31.10 (source panel + citation picker in the per-note/section editor, design: `docs/researcher-workspace-design.md`) | `GET /api/projects/{id}/documents/{doc}/link-check` (read-only report); wireframe `docs/wireframes/claim-evidence-panel.html` (X.14, awaiting scholar walkthrough) | `test_claim_linker.py`, `test_claim_link_check.py` | `[ ]` |
| 5.11.4 | Style help with diffs | M6.8; see also X.31.11 (AI side panel, accept/reject proposals, design doc) | — | — | `[ ]` |
| 5.11.5 | Ref-manager sync; CSL | M1.11, M6.5 | `POST …/sources/import`, `GET …/sources/export`, `GET …/zotero`, `POST …/zotero/pull` (G2-gated), `POST …/zotero/push` (verified only); `GET …/manuscript/export?format=md\|docx\|latex\|json\|bibtex` (CSL-style citations/references, `app/csl_styles.py`) | `test_refmanager*.py`, `test_zotero.py`, `test_csl_styles.py`, `test_manuscript_export.py` | M1.11 `[x]` (M1.11.1 `[x]`; M1.11.2, M1.11.3 `[x]` — P3 closures at user request 2026-10-10); M6.5 `[?]` (built 2026-10-18, needs review) |
| 5.11.6 | **Abstract, keywords, highlights, cover letter**; statements (incl. per-manuscript AI-use statement) | M6.10 *(new)*, M6.5, M6.6 (built 2026-10-18); AI-use statement also covered by X.31.12 (`provenance_report.py`, design doc) | `GET/PUT /projects/{id}/manuscript/statements` (data availability, conflict of interest, ethics — M6.5; generated `ai_use_disclosure` — M6.6); `GET/PUT /projects/{id}/manuscript/credit/{member_id}` (CRediT roles — M6.6); abstract/keywords/highlights/cover letter not built | `test_manuscript_export.py`, `test_ai_disclosure.py`, `test_credit.py` | M6.5 statements `[?]` (built 2026-10-18, needs review); M6.6 `[~]` (built 2026-10-18, needs review); rest `[ ]` |
| 5.11.7 | Similarity pre-check | M6.7; project-source similarity also covered by X.31.14 (`[?]`: API `POST/GET /projects/{id}/similarity-checks`; "Similarity" tab in the write step; tests `test_similarity.py`, `test_similarity_api.py`, `similarityLogic.test.mjs`), institutional integration point by X.31.15 (design doc) | — | — | `[ ]` |
| 5.11 gate | **G10 approve each section + AI-involvement level** | M6.1, M6.11 | `POST/GET …/documents/{doc}/approve`, `GET …/documents/{doc}/approval` (freezes AI-share from a current section provenance report); `routers/gates.py::decide_gate` refuses G10 approval while any section is unapproved or the manuscript has no sections | `test_section_approval.py`, role-matrix rows | `[?]` (built 2026-10-19, needs review) |
| 5.11 QC | Zero unresolved citations / orphan claims | M6.2 `[?]` (report only); M6.4 export block not built | `GET …/link-check` | `test_claim_linker.py`, `test_claim_link_check.py` | `[ ]` |

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
| §3.2 | Orchestrator: durable queue, pause, retry, re-entry | M0.2.1, M0.6.1–M0.6.5, M0.6.8–M0.6.10, X.1.1 | worker | `test_task_queue.py`, `test_task_queue_postgres.py` (real PostgreSQL: double claim, backoff, lease expiry) | M0.6.1–.11 `[x]` (M0 closed 2026-10-09); X.1.1 `[?]` |
| §3.2 | Object store for PDFs | M0.10.2, M0.10.3, M0.10.4 | `app/object_storage.py` | `test_object_storage.py` | M0.10.1–.4 `[x]` (M0.10.3 S3 backend, M0.10.4 Windows-safe keys; not verified against a real AWS/MinIO/R2 endpoint) |
| §3.3 | Citation verifier | M1.10.1–M1.10.6; human verify M0.2.4 | `POST .../sources/{id}/verify`, `POST .../sources/{id}/check` | `test_source_verify.py`, `test_citation_verifier.py`, `test_source_verification.py`, `test_citation_guard.py` | M0.2.4, M1.10.1–.6 `[x]` |
| §3.3 | Provenance logger (append-only) | M0.4.1–M0.4.5 | `GET .../audit`, `.../audit/export` | `test_audit_event_model.py`, `test_audit_wiring.py`, `test_audit_append_only.py`, `test_audit_api.py`, `test_created_by.py` | `[x]` (human sign-off X.30 "Audit log" area **approved 2026-10-09**) |
| §3.3 | Config service (discipline profile) | M0.7.1–M0.7.3 | project `discipline` / `config_json` | `test_discipline.py` | `[x]` (M0.7.3 reviewed 2026-10-09; 6 shipped profiles per Q3) |
| §3.3 | Cost / rate limiter | M0.9.1–M0.9.3 | `GET .../usage`, `PUT .../usage/budget` | `test_llm_usage.py`, `test_retrieval_run_cap.py`, `test_project_token_budget_override.py` | `[x]` |
| §4 | Paper model fields | M0.10.1 | — | `test_source_paper_fields.py` | `[x]` |
| §6 | Connector interface, caching, backoff, ToS flags | M1.3.1–M1.3.4 | `GET /api/connectors` | `test_connector_*.py` | `[x]` (M1.3 closed 2026-10-10, incl. M1.3.8 cross-process circuit breaker) |
| §6 | **ORCID/ROR author disambiguation** | X.18 *(new)* | — | — | `[ ]` |
| §7.1 | Retrieval-grounded; untrusted-text fencing | M1.2.1–M1.2.5, M3.3.1 | `untrusted_text.py`, `ai_extraction.py`, `evidence_extraction.v1.toml` | `test_untrusted_text.py`, `test_prompt_fencing.py`, `test_arc_fencing.py`, `test_adversarial.py`, `test_ai_extraction.py` | M1.2.1–.5 `[x]`; M3.3.1 `[x]` (independently reviewed; post-fix full suite passed) |
| §7.2 | JSON-schema outputs, reject + retry | M0.8.2, M0.8.6 | — | `test_llm_schema.py`, `test_llm_success_path.py` (fail-loudly paths), `test_structured_run.py` | M0.2.5, M0.8.2 `[x]`; M0.8.6 `[?]` |
| §7.3 | Citation guard (hard block) | M1.10.3, M1.10.5, M1.13.1, M6.4 | research runs (`research_run.citation_rejected`); source cards: verify / check buttons | `test_citation_guard.py` | M1.10.3–.5, M1.10.6, M1.13.1 `[x]`; **human sign-off X.30 approved 2026-10-10**; claims/export wait for M3.11/M6.4 |
| §7.4 | Numbers from code | M6.3 | — | — | `[ ]` |
| §7.5 | Confidence + abstention | M0.8.3, M0.8.6, M0.8.7 | `app/output_schemas.py` | `test_output_schemas.py`, `test_structured_run.py` | M0.8.3, M0.8.6, M0.8.7 `[?]` (M0.8.7 fixes a blank-answer defect found in review 2026-10-08) |
| §7.6 | Versioned prompts; log `prompt_version` + `model_id` | M0.8.1, M0.2.3 | `GET .../research-runs` (`provider_model`, `input_snapshot`) | `test_run_read_fields.py`, `test_prompts.py`, `test_prompt_provenance.py` | M0.2.3, M0.8.1 `[x]` |
| §7.7 | Evaluation harness / gold datasets | X.5, **X.15** | — | — | `[ ]` |
| §7.8 | Bias & coverage warnings | X.6 | — | — | `[ ]` |
| §7.9 | Data privacy / local model | M0.8.4, M0.8.8, M5.6 | `LOCAL_LLM_*` settings; local/private-host rule (`config.local_llm_url_problem`) | `test_llm_participant_data.py` | M0.8.4, M0.8.8 `[x]`; M5.6 `[ ]` |
| App. C | Stage state machine | M0.5.1, M0.5.7 | `stage` on project responses | `test_project_stage.py`, `test_stage_advance.py`, `test_reentry.py` | `[x]` (M0.5.1–M0.5.13 all closed; re-entry M0.5.5 owner-only choice human-confirmed 2026-10-09 via X.30) |

### §8 Gates (each must **block** the next step until a human approves)
| Gate | Stage | Gate task | Enforcement | Tests | Status |
|---|---|---|---|---|---|
| G1 | Idea scoping | M4.7 | M0.5.2–M0.5.4 `[x]` (gates stored, human-only decisions, tasks behind a pending gate stay blocked) | `test_gates.py`, `test_gate_decisions.py`, `test_gated_tasks.py` | gate `[x]`; stage content `[ ]` |
| G2 | Before bulk search | M1.8.1 | ″ | `test_search_gate.py` | `[x]` |
| G3 | After screening | M2.8.1 | ″ (decisions lock once G3 is approved, M2.1.3) | `test_screening.py`, `test_gate_required_conditions.py`, `test_role_matrix.py` | `[x]` (human approval, reason completeness and decision/undo lock verified) |
| G4 | After extraction | M3.6.2 | ″ | — | `[ ]` |
| G5 | After gap analysis | M3.9.3 | ″ | — | `[ ]` |
| G6 | After RQ/framework | M4.8 | gate/stage API; EC-8.2 | `test_exit_conditions.py`, `test_gate_required_conditions.py`, `test_guidance.py` | `[x]` condition enforcement implemented; approval remains human-only |
| G7 | Before data collection | M4.5 | gate/stage API | `test_gate_decisions.py`, `test_gate_reopen.py`, `test_research_types.py` | `[x]` mechanism/role verified; actual owner approval remains pending per gate integrity rules |
| G8 | Before analysis | M5.3 | ″ | — | `[ ]` |
| G9 | After analysis | M6.9 *(new)* | ″ | — | `[ ]` |
| G10 | Per manuscript section | M6.11 | ″; plus per-section `POST …/documents/{doc}/approve` | `test_section_approval.py` | `[?]` (built 2026-10-19, needs review) |
| G11 | Before submission | M7.5 | ″ | — | `[ ]` |
| all | Who may approve (role matrix) | M0.3.2, M0.3.4 | `project_access` role sets | `test_project_access.py`, `test_role_matrix.py` | `[x]` (human sign-off X.30 "Access control" area **approved 2026-10-09**) |
| all | Gates decided in order; reopen before the stage is reached | M0.5.10, M0.5.11, M0.5.13 | `decide_gate` 409 with `waiting_for`; `POST …/gates/{code}/reopen` | `test_gate_reopen.py` | `[x]` (M0.5.10, M0.5.11, M0.5.13 all closed) |

### §9 Integrity and ethics
| Requirement | Tasks | Tests | Status |
|---|---|---|---|
| No fabrication (hard to do accidentally) | M1.10, M3.4, M3.11.2, M6.2–M6.4 | `test_llm_success_path.py` (no placeholder output) | `[ ]` |
| AI-use disclosure from provenance log | M6.6 (built 2026-10-18: generated from the audit log, `app/ai_disclosure.py`, appended to every manuscript export); per-manuscript statement also covered by X.31.12 (design: `docs/researcher-workspace-design.md`) | `GET …/manuscript/statements` (`ai_use_disclosure`); `POST/GET /projects/{id}/provenance-reports`, `GET …/provenance-reports/{id}?format=md\|json` (X.31.12, `[~]`: report UI not built); tests `test_ai_disclosure.py`, `test_manuscript_export.py`, `test_provenance_report.py`, `test_provenance_reports_api.py`, `test_ledger_guard.py` | — | `[~]` |
| Authorship / CRediT; AI never an author; authorship ledger (who typed/dictated/pasted/AI-accepted each span) | M6.6 (built 2026-10-18: `app/credit.py`, the 14-term CRediT taxonomy, `ProjectMember.credit_roles`); authorship ledger covered by X.31.8–X.31.9, X.31.11 (append-only `authorship_events`, design doc) | `GET …/manuscript/credit`, `PUT …/manuscript/credit/{member_id}`; `POST …/documents/{id}/versions`, `GET …/versions`, `GET …/versions/{n}`, `POST …/versions/{n}/restore`, `GET …/ledger` (X.31.9) | `test_credit.py`, `test_manuscript_export.py`, `test_ledger.py`, `test_documents.py`, `test_ledger_guard.py` (X.31.8–X.31.9); X.31.11 not built | `[~]` |
| Copyright: store full text only where licensed | M2.7.1, M1.3.4, M1.4.8 | `test_oa_fetch.py`, `test_arxiv_fulltext.py` | `[x]` (M1.4.8 independently reviewed 2026-10-10) |
| Participant protection (consent, anonymisation, retention) | M4.5, M5.6, X.4 | — | `[ ]` |
| Reporting-guideline checklists | X.11 | — | `[ ]` |
| One-click reproducibility package | X.8.1 | `test_replication_export.py` | `[?]` |

### §10 Non-functional
| Area | Tasks | Tests | Status |
|---|---|---|---|
| Reliability (idempotent, resumable, no loss) | M0.2.1, M0.6.2, M0.6.4 | `test_worker_reaper.py` | `[?]` / `[ ]` |
| Security (roles, encryption, secrets) | M0.3.1–M0.3.5, X.4; existing sign-in (`/api/auth/*`) | `test_auth.py`, `test_project_access.py`, `test_role_matrix.py`, `test_project_members*.py`, `test_dashboard.py` (body limit) | `[?]` / `[ ]` |
| Auditability (immutable, exportable) | M0.4.1–M0.4.5 | `test_audit_*.py` | `[?]` |
| **Performance** (screening reference 113.8 abstracts/h local Ollama, re-scoped 2026-10-21; 500 papers indexed < 1 h) | X.19 | `backend/tests/test_performance_benchmark.py` (app overhead < 1% of the 31.6 s/abstract reference; model latency itself not measured in tests, see `milestone-reports/X.19-real-model-benchmark-2026-10-19.md`) | `[?]` |
| Scalability (multi-user, queue workers) | M0.3, M0.6 | — | `[ ]` |
| Usability (keyboard screening, side-by-side, diffs) | M2.3.1, M3.6.1, M6.8, X.14, X.32 | frontend suite, authenticated browser smoke | `[x]` for M2 keyboard/workspace UI; M3/M6 screens remain open |
| Portability (open export formats) | X.8, M1.11, M3.5.2, M6.5, X.34 (Obsidian vault, verified-only) | `test_obsidian_export.py` (X.34.1), `test_manuscript_export.py` (M6.5 md/docx/latex/json/bibtex) | `[ ]` (X.34.1 `[?]`; M6.5 `[?]`, built 2026-10-18 needs review) |
| Observability | X.7, M0.9.1 | — | `[ ]` |

### §11 Metrics
| Metric (target) | Tasks | Status |
|---|---|---|
| Search recall ≥ 95% on gold set | M1.8.3, X.15 | `[ ]` |
| Screening κ ≥ 0.8; false exclusion < 2% | M2.4.2, M2.9, X.15 | `[ ]` |
| Extraction ≥ 90% on critical fields | X.5, X.15 | `[ ]` |
| Citation verification 100% before export | M1.10, M6.4 | `[ ]` |
| Analysis re-run diff = 0 | M5.4 | `[?]` re-run identity check (M5.4.2) built, needs review |
| **Scholar outcomes** (time saved, corrections per 100 AI outputs, readiness) | X.20 *(new)* | `[ ]` |

### §13 Acceptance criteria (project definition of done)
| # | Criterion | Tasks | Status |
|---|---|---|---|
| 13.1 | Idea → verified evidence table + ranked gaps, full search log, PRISMA | M3.13.1, X.16 | `[x]` (independently reviewed API acceptance test in `test_m3_exit.py`) |
| 13.2 | Every citation resolves; unverifiable can't be exported | M1.10.3, M6.4 | `[ ]` (research-run answers are blocked and audited; export not built) |
| 13.3 | Every number traceable to an analysis run | M6.3, M5.3 | `[ ]` |
| 13.4 | Every AI artifact shows who/what/when/model + approval | M0.4, M0.8.1, **X.17**, X.38 | `[x]` data (M0.4, M0.8.1 closed), `[?]` UI (X.17 badge, awaiting review); research answers still have no approval step (X.38, open) |
| 13.5 | Re-enter any stage; downstream flagged stale | M0.5.5 | `[x]` (owner-only; human-confirmed 2026-10-09 via X.30; `test_reentry.py`) |
| 13.6 | One-action audit/replication package | X.8.1 | `[?]` (`GET …/export/replication`) |
| all | Automated acceptance suite | X.10 | `[ ]` |

---

## 3. Non-negotiables checked at every demo

| # | Check | Delivered by | Today |
|---|---|---|---|
| N1 | Every citation clicks through to a verified source; unverifiable is **blocked**, not warned | M1.10.3, M1.13, M3.5.1, M6.4 | **Built and human-signed-off for research runs:** answers citing unverified or unknown sources are blocked and audited (M1.10.3 `[x]`); the `evidence_synthesis.v3` prompt only allows verified sources (M1.10.5 `[x]`); source cards show verification status with check buttons (M1.13.1 `[x]`); the citation-block area of X.30 was approved by the user 2026-10-10. Export and claims are not built, so "blocked at export" is still untested (M3.11/M6.4). |
| N2 | Every AI output shows producer, model, date, approval status | M0.4, M0.8.1, X.17 | Data recorded (`created_by`, `provider_model`, `prompt_version` via M0.8.1, audit events); the Activity step shows the audit trail; X.17 (`[?]`) adds a per-output badge (producer, requester, model, prompt, date, approval/stale) on research runs and agent-retrieved sources. Research answers can't be approved yet (X.38). |
| N3 | The 11 gates block the next step until approved | M0.5.2–M0.5.4 + per-gate tasks in §8 table | Built for the gate mechanism (M0.5.2–.4 `[x]`: human-only approval, blocked tasks, stage advance needs the gate); only G2 and G3 have stage features behind them so far. Stage-by-stage gate tasks (§8) remain open. |
| N4 | Changing an earlier stage marks downstream work stale | M0.5.5 | Built and human-confirmed (M0.5.5 `[x]`: owner-only re-entry, stale artifacts, gates reset; owner-only design choice confirmed by the user 2026-10-09 via X.30). |
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
| X.31 researcher workspace: notes/ideas/decisions, authorship ledger, writing editor, similarity check (design: `docs/researcher-workspace-design.md`, approved pending owner sign-off + Q14–Q22) | From the master-requirements doc §3.2 (2026-10-05); closest spec lines §5.1 (idea capture, M4.7), §5.11.3/.4/.6/.7 (claim–evidence linker, style diffs, AI-use statement, similarity pre-check) and §9 (authorship/AI disclosure). |
| Existing dashboard + request body limit (`test_dashboard.py`) | Pre-plan app feature; §10 Security. |
| X.25 UI follow-ups (double submit, drawer focus, asset caching, nits; X.25.4 dropped; X.25.7 context cap) | Found in review of X.22–X.24; usability/robustness of existing UI. |
| X.24 project/context form submit bug | Defect fix in existing UI (found during X.23). |
| X.23 UI order fixes (source dialog, section order, phone jump links, context order) | User request 2026-10-03; usability of the existing project page (closest: §10 Usability). |
| X.22 responsive web + mobile layouts | User request 2026-10-03; usability of existing screens on phones/tablets (closest: §10 Usability). |
| X.32 three-pane step workspace UI + idea quick-capture (`ContextKind.idea`) | User request 2026-10-05; wires the already-built M0/M1 endpoints (criteria, seeds, searches, connectors, PRISMA, profile, members, audit, source verify/check/merge, stage re-entry) into real screens, covering M0.3.5/M0.5.6/M1.3.6/M1.13.1's UI subtasks; idea capture is project-scoped memory, not spec §5.1's AI clustering (that stays M4.7). Rule-32 wireframe waived by the scholar (Decisions log 2026-10-05). |
| X.34 Obsidian vault export (verified-only, default-off; X.34.1 `[?]`, X.34.2 button `[?]`, X.34.3 two-way sync design first) | User request 2026-10-06; closest spec line §10 Portability (open export formats). |
| X.33 workspace tidy / doc move into `docs/` | Repo hygiene; no spec line. |
| X.35 `api.js` error messages | Usability of existing UI. |
| B.1, B.2 backlog | Unscheduled; B.2 waits on Q5. |
| X.27 review backlog | Found via the master-requirements-doc audit (2026-10-05); clears the growing `[?]` second-review backlog — process gap, not a spec requirement. |
| X.40 step-by-step journey audit + design report (`docs/step-by-step-journey-audit.md`) | Owner-requested design/audit report; no code, no spec line. |
