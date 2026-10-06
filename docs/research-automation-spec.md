# End-to-End Academic Research Automation: Developer Specification

**Audience:** Developers building a system that automates the academic research lifecycle
**Design stance:** Model the system on how a *research scholar thinks*, not on how a pipeline is easiest to code. Every module maps to a scholarly decision, produces an auditable artifact, and keeps the scholar as the accountable author.

---

## 1. Purpose and Guiding Principles

### 1.1 Goal
Automate the repeatable, high-volume work of research (searching, screening, extracting, organizing, formatting, tracking) so the scholar can spend time on judgment-heavy work (framing the problem, interpreting evidence, building theory, defending claims).

### 1.2 Non-negotiable principles

| # | Principle | What it means for the build |
|---|-----------|-----------------------------|
| 1 | **Scholar is the author** | AI proposes; the human decides. Every consequential step has an approval gate. |
| 2 | **No fabricated evidence** | Every citation, quote, statistic, and claim must trace to a verified source record (DOI/ID + page/section). Unverifiable output is blocked, not warned. |
| 3 | **Provenance everywhere** | Each artifact stores who/what produced it, from which inputs, with which model/version/prompt, and when. |
| 4 | **Reproducibility** | Searches, screening decisions, and analyses can be re-run and produce an audit trail (PRISMA-style). |
| 5 | **Iterative, not linear** | Research loops back (a gap found in writing sends you back to search). Architecture must support re-entry at any stage. |
| 6 | **Discipline-configurable** | Methods, citation styles, databases, and rigor norms vary by field. Make them config, not code. |
| 7 | **Transparent AI use** | The system logs AI involvement per section to support journal/university AI-disclosure requirements. |

---

## 2. The Scholar's Mental Model (the spine of the system)

A scholar moves through these phases, and the questions they ask at each phase define the module requirements.

```
 ┌─────────────────────────────────────────────────────────────────────┐
 │ 1 Idea → 2 Scoping → 3 Literature → 4 Gap & RQ → 5 Theory/Framework │
 │      ↑                                                   ↓          │
 │ 12 Publish ← 11 Revise ← 10 Write ← 9 Interpret ← 8 Analyse ← 7 Collect ← 6 Design │
 │      └──────────── continuous: Reference mgmt, Notes, Audit log ────┘│
 └─────────────────────────────────────────────────────────────────────┘
```

| Phase | The scholar asks… | System responsibility |
|---|---|---|
| 1. Idea | "What puzzle or problem matters?" | Capture, cluster, and pressure-test ideas |
| 2. Scoping | "Is this researchable, novel, feasible?" | Quick landscape scan, feasibility score |
| 3. Literature | "What is already known, by whom, how, and how well?" | Systematic search, screen, extract, map |
| 4. Gap & RQ | "What's unanswered, and what exactly will I ask?" | Gap mining, RQ/hypothesis drafting & critique |
| 5. Theory | "Which lens explains this? What are the constructs?" | Construct inventory, theory mapping, conceptual model |
| 6. Design | "How can I credibly answer this?" | Method selection, sampling, instrument, ethics |
| 7. Collection | "Is my data valid and complete?" | Survey/data pipelines, quality checks |
| 8. Analysis | "What do the data show, and is it robust?" | Reproducible stats/qual coding pipelines |
| 9. Interpretation | "What does it mean relative to prior work?" | Findings-to-literature linking |
| 10. Writing | "What is my argument and is it supported?" | Structured drafting with claim-evidence links |
| 11. Revision | "Where would a reviewer attack this?" | Simulated review, consistency checks |
| 12. Publication | "Right venue, right format, right ethics?" | Journal matching, formatting, submission tracking |

---

## 3. System Architecture

### 3.1 High-level components

```
┌──────────────┐   ┌───────────────────────┐   ┌──────────────────┐
│  Web/CLI UI  │──▶│  Orchestrator (state  │──▶│  Agent/Worker    │
│  (scholar)   │◀──│  machine + task queue)│◀──│  pool (per phase)│
└──────────────┘   └──────────┬────────────┘   └────────┬─────────┘
                              │                         │
                ┌─────────────▼─────────┐     ┌─────────▼──────────┐
                │ Project Knowledge Store│    │ External connectors │
                │ (DB + vector + files)  │    │ (scholarly APIs)    │
                └─────────────┬──────────┘    └────────────────────┘
                              │
                  ┌───────────▼───────────┐
                  │ Audit / Provenance log│
                  └───────────────────────┘
```

### 3.2 Recommended building blocks
- **Orchestrator:** workflow engine with durable state (Temporal, Prefect, or LangGraph + Postgres). Must support pause-for-human, retry, and re-entry.
- **Storage:**
  - Relational DB (Postgres) for projects, papers, decisions, claims
  - Vector store (pgvector/Qdrant) for semantic retrieval over the full-text corpus
  - Object store for PDFs, datasets, drafts
  - Graph layer (optional) for citation/construct networks
- **LLM layer:** model-agnostic adapter; prompts versioned in the repo; structured (JSON-schema) outputs wherever downstream code consumes them.
- **Connectors (see §6)** isolated behind interfaces so sources can be swapped.
- **Human-in-the-loop (HITL) service:** review queue with accept/edit/reject, bulk actions, and keyboard-driven screening UI.

### 3.3 Cross-cutting services
- **Citation Verifier:** resolves every reference against Crossref/OpenAlex; flags non-existent or mismatched metadata.
- **Provenance Logger:** append-only events (`actor`, `action`, `inputs`, `outputs`, `model`, `prompt_version`, `timestamp`).
- **Config Service:** discipline profile (citation style, databases, methods, reporting guideline).
- **Cost/Rate Limiter:** API quotas, token budgets per project.

---

## 4. Core Data Model

```text
Project(id, title, discipline, status, config_json, created_at)
ResearchIdea(id, project_id, text, tags, novelty_score, feasibility_score, status)
SearchQuery(id, project_id, database, query_string, filters, run_at, n_results, version)
Paper(id, doi, title, authors[], year, venue, abstract, oa_url, source_ids{}, fulltext_path, quality_flags)
ScreeningDecision(id, paper_id, stage[title_abstract|full_text], decision, reason_code, decided_by[human|ai], confidence, timestamp)
Extraction(id, paper_id, schema_version, fields_json, evidence_spans[], verified_by_human)
Construct(id, name, definition, source_paper_ids[], measures[], synonyms[])
Theory(id, name, core_propositions[], source_paper_ids[])
Gap(id, type[theoretical|methodological|empirical|contextual|population], statement, supporting_paper_ids[], priority)
ResearchQuestion(id, text, type, linked_gap_ids[], status)
Hypothesis(id, rq_id, statement, direction, constructs[], status)
Design(id, rq_id, method, sampling, instruments[], analysis_plan, ethics_status)
Dataset(id, source, schema, n, quality_report, version, hash)
AnalysisRun(id, dataset_id, script_path, env_hash, outputs[], seed, timestamp)
Claim(id, text, section_id, evidence_links[], strength, verified)
Section(id, manuscript_id, heading, text, ai_involvement_level, version)
Manuscript(id, project_id, target_venue, status, version)
Reference(id, paper_id, cited_in_section_ids[], style_formatted)
ReviewComment(id, manuscript_id, source[simulated|real], text, resolution)
Submission(id, manuscript_id, venue, submitted_at, status, decision_history[])
AuditEvent(id, project_id, actor, action, payload_json, timestamp)
```

**Key invariant:** `Claim.evidence_links` must reference `Extraction`/`Paper`/`AnalysisRun` records. A claim with no evidence link cannot be marked `verified`.

---

## 5. Module Specifications (by scholarly phase)

Each module lists: **Scholar's thinking → Inputs → Automated tasks → Outputs → Human gate → Quality checks**.

### 5.1 Idea Capture and Scoping
- **Scholar's thinking:** Is this a real puzzle? Has it been done? Can I do it with my resources and timeline?
- **Inputs:** free-text ideas, voice notes, advisor feedback, seed papers, personal constraints (time, data access, methods skills).
- **Automated tasks:**
  1. Cluster and de-duplicate ideas; extract candidate constructs and contexts.
  2. Run a **rapid landscape scan** (top-cited, recent reviews, volume trend over time).
  3. Compute a **novelty heuristic** (semantic distance from existing literature) and a **feasibility checklist** (data availability, method fit, ethics risk).
- **Outputs:** Idea brief (1 page): problem, why it matters, landscape snapshot, risks.
- **Human gate:** Scholar selects/refines the idea before heavy search begins.
- **Quality checks:** Novelty score must show its comparator papers (no black-box scores).

### 5.2 Systematic Literature Search
- **Scholar's thinking:** What are my concepts and synonyms? Which databases cover my field? How do I avoid missing key work?
- **Inputs:** Idea brief, construct list, inclusion/exclusion criteria (PICO/PICOC/SPIDER or discipline equivalent).
- **Automated tasks:**
  1. **Query builder:** generate Boolean strings from concept blocks + synonyms (AND across concepts, OR within); adapt syntax per database.
  2. **Multi-source retrieval** (see §6), de-duplicate by DOI → normalized title+year+first author.
  3. **Snowballing:** backward (references) and forward (citing papers) from seed/core papers, iterated N rounds.
  4. **Alert subscriptions:** re-run saved queries on a schedule and surface new papers.
  5. **Log everything** for PRISMA: counts per source, per stage, per exclusion reason.
- **Outputs:** Deduplicated corpus, versioned search log, auto-generated PRISMA flow numbers.
- **Human gate:** Approve search strings and criteria *before* bulk retrieval; review a sample of results for recall sanity-check (known-item test: do the seed papers appear?).
- **Quality checks:** Recall test against a "gold set" of known relevant papers; report which gold papers were missed and why.

### 5.3 Screening (Title/Abstract → Full-Text)
- **Scholar's thinking:** Does this paper meet my criteria? When uncertain, include for now and decide on full text.
- **Automated tasks:**
  1. AI **pre-screen with rationale** against explicit criteria; output `include | exclude | maybe` + reason code + confidence.
  2. **Active-learning prioritization:** rank unscreened papers by predicted relevance after each human batch.
  3. **Dual-screen mode:** two independent reviewers (human or human+AI); compute Cohen's kappa; route conflicts to a resolution queue.
  4. Fetch open-access full texts (Unpaywall/OpenAlex/CORE); flag paywalled items for manual upload.
- **Outputs:** Screening decisions with reason codes, PRISMA counts, inter-rater statistics.
- **Human gate:** Humans make final decisions; AI can never auto-exclude below a configurable confidence threshold without human sampling audit (e.g., audit 10% of AI excludes).
- **Quality checks:** Track AI–human agreement; stop AI pre-screening if agreement drops below threshold.

### 5.4 Data Extraction and Evidence Tables
- **Scholar's thinking:** What must I capture from every study to compare them fairly? (Context, sample, method, constructs, measures, findings, limitations.)
- **Automated tasks:**
  1. Define an **extraction schema** (versioned, discipline-specific). Example fields: research question, theory used, constructs, measures/scales, sample & context, method, key findings with effect sizes, limitations, future-research suggestions.
  2. LLM extraction with **evidence spans**: each field value must include the supporting quote and page/section anchor.
  3. **Verification pass:** confirm quote exists verbatim in the full text; mark mismatches.
  4. Build a **matrix view** (papers × fields), exportable to CSV/Excel.
- **Outputs:** Evidence table where every cell links back to source text.
- **Human gate:** Spot-check or fully verify high-stakes fields (effect sizes, sample sizes, measures).
- **Quality checks:** Reject extractions with no matching span; confidence score per field; double-extraction on a sample.

### 5.5 Synthesis, Construct Mapping and Gap Analysis
- **Scholar's thinking:** What themes, debates, and contradictions exist? What has been studied, in whom, with what methods, and what is conspicuously missing?
- **Automated tasks:**
  1. **Thematic clustering** (topic modeling/embedding clustering) with human-editable labels.
  2. **Construct inventory:** list constructs, definitions across authors, measurement scales, and conceptual overlaps/jingle-jangle issues (same word, different meaning; different words, same meaning).
  3. **Theory map:** which theories are applied, where, and with what results.
  4. **Method/context matrices** to reveal under-researched cells (e.g., method × population × geography).
  5. **Gap miner:** classify gaps (theoretical, methodological, empirical, contextual, population, temporal) — each must cite ≥ 2 supporting papers or matrix evidence. Include authors' own "future research" statements.
  6. **Bibliometric views:** co-citation, bibliographic coupling, keyword co-occurrence, publication trends (VOSviewer/CiteSpace-compatible exports).
- **Outputs:** Thematic synthesis draft, construct table, theory map, ranked gap list.
- **Human gate:** Scholar curates and prioritizes gaps; gaps without evidence are rejected.
- **Quality checks:** Contradiction detection (studies with opposing findings flagged with both citations).

### 5.6 Research Questions, Hypotheses and Conceptual Framework
- **Scholar's thinking:** Is my question answerable, significant, and aligned with a gap? Are my constructs clearly defined and measurable?
- **Automated tasks:**
  1. Draft RQs/hypotheses from selected gaps; run a **critique loop** (clarity, testability, scope, novelty, FINER criteria).
  2. Propose **conceptual model**: constructs, relations (direct, mediation, moderation), with each path linked to supporting literature.
  3. Generate a diagram (Mermaid/Graphviz/SVG) editable by the scholar.
  4. Check **construct–measure alignment** (each construct has ≥ 1 validated measure).
- **Outputs:** RQ set, hypothesis list with theoretical justification, conceptual model diagram.
- **Human gate:** Scholar owns the final RQs and theory.
- **Quality checks:** Every hypothesis cites its theoretical basis and prior empirical support.

### 5.7 Research Design and Ethics
- **Scholar's thinking:** What design gives credible evidence? What are threats to validity? Who could be harmed?
- **Automated tasks:**
  1. **Method recommender** (quant/qual/mixed) with trade-offs explained.
  2. **Sampling and power:** sample-size calculators (G*Power-style logic), sampling frame notes.
  3. **Instrument builder:** assemble scales from the validated-measure database; generate survey/interview guide; pilot-test checklist; reliability/validity plan (Cronbach's α, CFA, AVE, HTMT, etc., as applicable).
  4. **Ethics pack:** draft consent form, information sheet, data management plan, IRB/ethics-committee application skeleton.
  5. **Pre-registration draft** (OSF/AsPredicted format) for hypotheses and analysis plan.
- **Outputs:** Design document, instrument, ethics documents, pre-registration draft.
- **Human gate:** Mandatory; nothing is sent to participants or committees without scholar sign-off.
- **Quality checks:** Flag common-method bias risks, leading questions, double-barreled items.

### 5.8 Data Collection and Management
- **Scholar's thinking:** Is the data trustworthy, complete, and handled ethically?
- **Automated tasks:**
  1. Deploy survey (Qualtrics/Google Forms/LimeSurvey API) or ingest existing datasets.
  2. Monitor responses: completion rate, attention-check failures, straight-lining, speeders, duplicates, quota fill.
  3. **Data cleaning pipeline** with logged, reversible steps; separate raw vs. processed data (raw is immutable).
  4. Anonymization/pseudonymization utilities; codebook auto-generation.
  5. Qualitative: transcription (speech-to-text), anonymization, storage.
- **Outputs:** Versioned, hashed datasets; data quality report; codebook.
- **Human gate:** Approve exclusion rules before applying; never silently drop cases.
- **Quality checks:** Every transformation recorded; dataset hash stored on each analysis run.

### 5.9 Analysis
- **Scholar's thinking:** Does the analysis match the design? Are assumptions met? Are results robust, not cherry-picked?
- **Automated tasks (quantitative):**
  1. Generate analysis scripts (Python/R) from the pre-registered plan: descriptives, assumptions tests, reliability, EFA/CFA, regression, SEM/PLS-SEM, mediation/moderation, multilevel, etc.
  2. Run in a **pinned environment** (container + lockfile + fixed seeds).
  3. **Robustness suite:** alternative specifications, outlier sensitivity, missing-data handling, multiple-comparison correction.
  4. Auto-format tables/figures to journal style; export reproducible notebooks.
- **Automated tasks (qualitative):**
  1. Assist with open → axial → selective coding or reflexive thematic analysis; AI suggests codes, **human accepts/merges/renames**.
  2. Maintain codebook with definitions and exemplar quotes; compute inter-coder agreement.
  3. Link each theme to supporting excerpts.
- **Outputs:** Results tables, figures, analysis logs, replication package.
- **Human gate:** Scholar reviews assumptions, model choice, and interpretation; deviations from pre-registration must be documented.
- **Quality checks:** Re-run from scratch must reproduce identical numbers; AI never "reports" a number it did not compute in code.

### 5.10 Interpretation and Discussion
- **Scholar's thinking:** What do these results mean? Do they confirm, extend, or contradict prior work and theory? What are the implications and limits?
- **Automated tasks:**
  1. Link each finding to prior studies that agree/disagree (from the evidence table).
  2. Draft theoretical contributions, practical implications, limitations, and future-research directions with evidence links.
  3. Check **overclaiming** (causal language from correlational design, generalization beyond sample).
- **Outputs:** Findings-to-literature map, discussion outline.
- **Human gate:** Scholar writes or approves the contribution statement.

### 5.11 Writing and Manuscript Assembly
- **Scholar's thinking:** What is my one-sentence argument? Does each section earn its place? Is every claim supported?
- **Automated tasks:**
  1. **Argument outline** first (claims → evidence), then section drafts.
  2. Section templates by genre (IMRaD, conceptual paper, review paper, thesis chapter).
  3. **Claim–evidence linker:** inline citations are inserted only from verified `Reference` records; every factual sentence can be traced via hover/side panel.
  4. Style assistance (clarity, concision, discipline tone) that preserves the author's voice; show diffs, never silently overwrite.
  5. Reference manager sync (Zotero/Mendeley/BibTeX); auto-format to the target style (APA, Harvard, Chicago, IEEE, etc.) via CSL.
  6. Generate abstract, keywords, highlights, cover letter, and statements (data availability, conflict of interest, AI-use disclosure, ethics).
  7. **Plagiarism/similarity pre-check** and self-overlap warnings (integrate with iThenticate/Turnitin where licensed).
- **Outputs:** Manuscript in DOCX/LaTeX/Markdown, clean reference list, submission statements.
- **Human gate:** Scholar approves each section; AI-involvement level is recorded per section.
- **Quality checks:** Zero unresolved citations; zero "orphan" claims; reference list ↔ in-text citations match both ways.

### 5.12 Revision, Peer-Review Simulation and Response Management
- **Scholar's thinking:** How would a skeptical reviewer attack this? How do I respond constructively to real reviews?
- **Automated tasks:**
  1. **Simulated reviewer personas** (theory, methods, contribution, writing) producing structured critiques.
  2. Consistency checker: constructs defined once and used consistently; hypotheses ↔ results ↔ conclusions alignment; numbers in text = numbers in tables.
  3. For real reviews: parse comments into an **issue tracker**, map each to manuscript locations, draft response-letter skeleton, track resolution status, and produce redline/diff.
- **Outputs:** Pre-submission checklist, response-to-reviewers document.
- **Human gate:** Scholar writes final responses.

### 5.13 Venue Selection, Submission and Tracking
- **Scholar's thinking:** Which journal fits the topic, method, rigor, and my career/PhD requirements (e.g., indexed in Scopus/WoS/UGC-CARE)? Is it legitimate?
- **Automated tasks:**
  1. **Journal matcher:** semantic similarity with recent articles + scope, indexing status, APC/open-access, review time, acceptance signals.
  2. **Predatory-journal screening** using multiple lists/signals (Scopus/DOAJ/Cabells/UGC-CARE as configured) with explainable flags.
  3. Format to author guidelines (word limits, structure, reference style, figure specs).
  4. Submission tracker: dates, status, reviewer rounds, deadlines, reminders; calendar integration.
  5. Post-acceptance: ORCID linking, preprint deposit, data/code archiving (Zenodo/OSF), conference/CFP alerts.
- **Outputs:** Ranked venue shortlist with rationale, formatted submission package, tracking dashboard.

---

## 6. External Integrations

| Need | Candidate sources/APIs | Notes |
|---|---|---|
| Scholarly metadata & citations | **OpenAlex**, **Crossref**, **Semantic Scholar API**, **OpenCitations** | Free, good for dedupe, snowballing, citation graphs |
| Indexed databases | **Scopus**, **Web of Science**, **IEEE Xplore**, **PubMed/Entrez**, **ProQuest**, **EBSCO**, **JSTOR** | Licensing required; respect ToS; use official APIs, not scraping |
| Open-access full text | **Unpaywall**, **CORE**, **arXiv**, **SSRN**, **PMC**, **Europe PMC** | Check license before storing/processing |
| Preprints/repositories | arXiv, OSF, Zenodo, SSRN | |
| General web search, crawl, PDF extraction | ARC retrieval microservice (HTTP only) | Opt-in per run, off by default; results enter as **unverified** sources and must pass the Citation Verifier before use (added 2026-10-03) |
| Reference managers | **Zotero API**, Mendeley, BibTeX | Two-way sync |
| Identity | **ORCID**, ROR | Author disambiguation |
| Journal data | Scopus Sources, **DOAJ**, SCImago, Cabells, UGC-CARE (India) | Verify via official lists |
| Survey tools | Qualtrics, LimeSurvey, Google Forms | |
| Stats/compute | Python (pandas, statsmodels, scikit-learn, semopy), R (lavaan, psych, plspm/seminr) | Containerized |
| Writing/export | Pandoc, CSL, LaTeX, python-docx | |
| Notifications/calendar | Email, Google Calendar | For alerts and deadlines |

**Integration rules:** wrap each connector behind a common interface (`search()`, `get_by_id()`, `get_citations()`, `get_fulltext()`); implement caching, backoff, rate-limit handling, and per-source ToS compliance flags.

---

## 7. AI/LLM Design Guidelines

1. **Retrieval-grounded generation only** for anything factual: retrieve passages → generate with cited spans → verify spans exist.
2. **Structured outputs:** JSON-schema validated; reject and retry on schema violations.
3. **Citation guard (hard block):** after any draft generation, run `verify_citations()`:
   - DOI/ID resolves in Crossref/OpenAlex
   - Title/authors/year match the claimed reference
   - Quoted text exists in the stored full text
   Failures stop the pipeline step and surface to the user.
4. **Numbers come from code, not language:** statistics are computed by scripts and inserted programmatically.
5. **Confidence and abstention:** the model must be able to say "insufficient evidence"; surface low-confidence items for human review.
6. **Prompt/version management:** prompts in version control with tests; log `prompt_version` + `model_id` on every output.
7. **Evaluation harness:** maintain gold datasets (screening labels, extraction answers, known-gap examples) and run regression tests on every prompt/model change.
8. **Bias and coverage awareness:** warn about English-language bias, database coverage gaps, and citation-count bias (rich-get-richer) in rankings.
9. **Data privacy:** no participant-identifiable data sent to third-party models unless approved; support local/self-hosted models for sensitive qualitative data.

---

## 8. Human-in-the-Loop Gate Map

| Gate | Stage | Required approval |
|---|---|---|
| G1 | After idea scoping | Approve topic direction |
| G2 | Before bulk search | Approve search strategy + criteria |
| G3 | After screening | Approve included set + audit sample |
| G4 | After extraction | Verify critical fields |
| G5 | After gap analysis | Select and justify gaps |
| G6 | After RQ/framework | Approve RQs, hypotheses, model |
| G7 | Before data collection | Approve design, instrument, ethics |
| G8 | Before analysis | Lock analysis plan |
| G9 | After analysis | Approve interpretation |
| G10 | Per manuscript section | Approve text and AI-use level |
| G11 | Before submission | Final author sign-off, declarations |

Gates are enforced by the orchestrator: downstream tasks stay in `blocked` state until the gate is `approved`.

---

## 9. Quality, Integrity and Ethics Requirements

- **Research integrity:** no fabrication, falsification, or plagiarism; system must make these *hard to do accidentally*.
- **AI disclosure:** generate an AI-use statement from the provenance log, adapted to the journal/university policy.
- **Authorship:** system never lists AI as an author; contributions recorded per human author (CRediT taxonomy).
- **Copyright:** store/process full text only where licensing permits; keep links and metadata otherwise.
- **Participant protection:** consent tracking, anonymization, retention policies, GDPR/DPDP-style compliance configurable.
- **Reporting standards:** attach relevant guideline checklists (PRISMA, PRISMA-ScR, SRQR, COREQ, CHERRIES, STROBE, APA JARS, etc.) based on design.
- **Reproducibility package:** one-click export (data dictionary, scripts, environment, search logs).

---

## 10. Non-Functional Requirements

| Area | Requirement |
|---|---|
| Reliability | Idempotent tasks; resumable workflows; no data loss on crash |
| Security | Encrypted at rest/in transit; role-based access (owner, co-author, supervisor, reviewer); secrets vaulted |
| Auditability | Immutable audit log exportable per project |
| Performance | Screen ≥ 1,000 abstracts/hour (AI-assisted); full-text indexing of 500 papers in < 1 hour |
| Scalability | Multi-project, multi-user; queue-based workers |
| Usability | Keyboard-first screening, side-by-side PDF + extraction view, diff-based editing |
| Portability | Export to open formats: CSV, BibTeX/RIS, DOCX, LaTeX, Markdown, JSON |
| Observability | Per-step metrics, token/cost tracking, error dashboards |

---

## 11. Metrics and Evaluation

**System quality**
- Search recall on gold set (target ≥ 95% for known-item test)
- Screening: AI–human agreement (κ ≥ 0.8), false-exclusion rate on audited sample (< 2%)
- Extraction: field-level precision/recall vs. human gold (target ≥ 90% on critical fields)
- Citation verification pass rate (target 100% before export)
- Reproducibility: re-run diff = 0 for analysis outputs

**Scholar outcomes**
- Time saved per phase (baseline vs. assisted)
- Number of human corrections per 100 AI outputs (should decline over time)
- Manuscript readiness checklist completion

---

## 12. Suggested Delivery Roadmap

| Milestone | Scope | Exit criteria |
|---|---|---|
| **M0: Foundations** | Data model, auth, project workspace, audit log, config service | Create project, log events |
| **M1: Literature core** | Query builder, OpenAlex/Crossref/Semantic Scholar connectors, dedupe, Zotero sync, PRISMA counts | Reproducible search with logs |
| **M2: Screening** | Screening UI, AI pre-screen with rationale, dual-review + kappa, full-text fetch | Audit-ready screening |
| **M3: Extraction & synthesis** | Schema-driven extraction with evidence spans, matrices, construct map, gap miner | Verified evidence table + ranked gaps |
| **M4: Framework & design** | RQ/hypothesis critic, conceptual model builder, instrument + ethics pack, pre-registration | Design document exportable |
| **M5: Data & analysis** | Survey ingestion, cleaning pipeline, containerized analysis, qualitative coding assist | Reproducible results package |
| **M6: Writing & integrity** | Claim-evidence linker, citation guard, section drafting, style formatting, AI-use disclosure | Zero-orphan-claim manuscript |
| **M7: Review & publication** | Reviewer simulation, journal matcher, predatory screening, submission tracker | End-to-end demo project |

**MVP recommendation:** M0 → M3. The literature review is the highest-volume, most automatable part, and it produces immediate value.

---

## 13. Acceptance Criteria (end-to-end)

1. A scholar can go from a rough idea to a **verified evidence table and ranked gap list** with a complete search log and PRISMA counts.
2. Every citation in any generated text **resolves** and matches its source; unverifiable references cannot be exported.
3. Every number in the manuscript is **traceable to an analysis run** with a stored script, data hash, and environment.
4. Every AI-generated artifact shows **who/what/when/which model** and its human approval status.
5. The scholar can **re-enter any stage** and downstream artifacts are flagged as stale with a clear re-run path.
6. A **full audit/replication package** can be exported in one action.

---

## 14. Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Hallucinated references/claims | Citation guard, evidence-span verification, retrieval-grounded generation |
| Over-reliance / deskilling | Mandatory gates, explanation of AI rationales, "show your work" views |
| Database coverage bias | Multi-source search, coverage reports, manual-add path |
| Licensing/ToS violations | Official APIs only, per-source compliance flags |
| Privacy breaches with participant data | Local models option, redaction before LLM calls, access controls |
| Paper-mill / predatory venues | Multi-signal venue vetting with explainable flags |
| Reproducibility drift | Pinned environments, hashed data, seeded runs |
| Scope creep | Phase-gated roadmap, MVP first |

---

## 15. Open Questions for the Product Owner

1. Primary discipline(s) and the reporting guidelines/citation styles to ship first?
2. Which licensed databases (Scopus, WoS, etc.) are available for API access?
3. Is local/self-hosted LLM deployment required for sensitive data?
4. Single-scholar tool or multi-user lab/institution platform (supervisor review, co-authors)?
5. Target outputs: journal articles only, or theses/dissertations and grant proposals too?
6. Institutional policies on AI use that the disclosure generator must follow?

---

## Appendix A: Example Boolean Query Template

```text
("construct A" OR "synonym A1" OR "synonym A2")
AND ("construct B" OR "synonym B1" OR "synonym B2")
AND ("context term 1" OR "context term 2")
Filters: year >= 2010; language = English; doc type = article OR review;
         peer-reviewed = true
```

## Appendix B: Example Extraction Schema (excerpt)

```json
{
  "schema_version": "1.0",
  "fields": {
    "research_question": {"type": "string", "evidence_required": true},
    "theory_used": {"type": "array", "items": "string", "evidence_required": true},
    "constructs": {"type": "array", "items": {"name": "string", "definition": "string"}},
    "measures": {"type": "array", "items": {"construct": "string", "scale": "string", "reliability": "number|null"}},
    "sample": {"n": "integer", "population": "string", "country": "string"},
    "method": {"design": "string", "analysis": "string"},
    "key_findings": {"type": "array", "items": {"statement": "string", "effect_size": "string|null", "p_or_ci": "string|null"}},
    "limitations": {"type": "array", "items": "string"},
    "future_research": {"type": "array", "items": "string"}
  },
  "rules": "Every non-null field must include evidence: {quote, page, section}"
}
```

## Appendix C: Reference Workflow State Machine (simplified)

```text
IDEA → SCOPED(G1) → SEARCH_PLANNED(G2) → RETRIEVED → SCREENED(G3)
→ EXTRACTED(G4) → SYNTHESIZED → GAPS_SELECTED(G5) → FRAMEWORK(G6)
→ DESIGN_APPROVED(G7) → DATA_COLLECTED → PLAN_LOCKED(G8) → ANALYZED(G9)
→ DRAFTED(G10) → REVISED → SUBMISSION_READY(G11) → SUBMITTED → [REVISION_LOOP | ACCEPTED]
Any state → (re-entry) → earlier state, with downstream artifacts marked STALE.
```
