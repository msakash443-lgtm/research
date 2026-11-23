# Changelog

All notable user-facing changes to this app are recorded here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Internal-only work (plan.md bookkeeping, test-only changes, refactors with no behaviour change) is not listed.

**Backfill note.** The repo has no git tags. The entries below cover the git history from 2026-09-10 to 2026-11-21 (89 commits) and are grouped under the app version reported by `backend/app/main.py` (`0.2.0`). Commit hashes are given so each entry can be traced with `git show <hash>`. Entries were derived from commit messages and the plan.md milestone closures, not from a line-by-line diff.

## [Unreleased]

No entries yet.

## [0.2.0] - backfill, 2026-09-10 to 2026-11-21

### Foundations and M0 (2026-09-10 to 2026-10-09)

- **Added:** access control, audit log, citation verification, ARC retrieval integration and screening with AI pre-screen (`76e15b3`, 2026-10-06).
- **Added:** snowballing and research source connectors (`f6b428d`, `f835c8b`, 2026-10-08).
- **Added:** M0 closed with S3 object storage backend, discipline profiles, token budget override, and access-control and audit-log sign-off (`c2280c6`, 2026-10-09).
- **Changed:** app moved from `research/` to the repository root; reference docs moved to `docs/` (`76e15b3`, 2026-10-06).
- **Fixed:** CI workflow working directory and run defaults (`9d27e23`, `257199d`, 2026-10-06).

### Literature core, M1 (2026-10-10)

- **Added:** saved-query alerts: subscribe, list, run, delete, and a sweep that records new-versus-union results (`d1a5cb7`, merged `b858515`).
- **Added:** Zotero two-way sync, off by default. Pull imports Zotero items as sources; push sends verified sources only (`d1a5cb7`).
- **Changed:** BibTeX export writes each field on a single line (`d1a5cb7`).

### Screening, AI controls and workspace (2026-10-13 to 2026-10-16)

- **Added:** M2 screening and AI controls, reconciled with M3 extraction and claims (`d4fba78`, `1bba91c`, 2026-10-13).
- **Added:** workspace AI, ledger, provenance reports, similarity, manuscript export, and account erasure (`6d664fc`, 2026-10-14).
- **Added:** citation counts, research briefs, and design tools (`856ed52`, `6358f5e`, 2026-10-15).
- **Changed:** shared step navigation and a Results flow in the web UI (`ed1d857`, 2026-10-16).
- **Fixed:** Postgres task-queue tests run serially to avoid a `CREATE TYPE` race (`194ad9a`, 2026-10-14).

### Analysis, qualitative coding and drafting (2026-10-17 to 2026-10-19)

- **Added:** analysis plans and runner, datasets and dataset versions, preregistration, validity plans, ethics pack, exclusion rules, measure library, and robustness checks (`2a20207`, 2026-10-18).
- **Added:** qualitative coding with codebook and transcripts; idea briefs, idea clustering and scoping, landscape scan and semantic novelty (`2a20207`).
- **Added:** CSL citation styles, number citation, and manuscript checks (`2a20207`).
- **Added:** survey support, including Google Forms (`2a20207`).
- **Added:** AI disclosure and credit statements (`2a20207`).
- **Added:** section drafting with an approval gate, interpretation findings, own-paper lookup, notebook export, and retention purge (`8797399`, 2026-10-19).

### Privacy, reporting, observability and journals (2026-10-22)

- **Added:** privacy profiles (default, GDPR, DPDP), processor register, personal-data export, and project archive and lifecycle (`70b52e0`).
- **Added:** accuracy report, bias warnings, deviation log, full-text coverage, overclaim and number-consistency checks (`70b52e0`).
- **Added:** journal tables, reporting checklists, genre templates, manuscript format, and AI-disclosure formats (`70b52e0`).
- **Added:** observability dashboard, LLM pricing and usage reservations, pagination, and structured logging (`4169c47`).
- **Changed:** rate limiting and content-length limits in middleware; LLM retry and task runner hardened (`4169c47`).
- **Added:** M6 and M7 front matter, review workflows, journal screening, and submissions (`7289ad8`).

### Fixes and identifiers (2026-10-23 to 2026-10-24)

- **Changed:** qualitative codes already in the codebook (active, renamed or merged) no longer need a definition; a supplied definition for a known code is ignored with a note in the response (`e990527`).
- **Fixed:** Google Forms response fetch is capped at 100 pages (`e990527`).
- **Changed:** processor register lists full-text hosts only for enabled connectors (`e990527`).
- **Added:** validated ORCID and ROR identifiers for people and institutions (`949e5fe`).
- **Fixed:** number-consistency check uses half-up rounding (`949e5fe`).
- **Added:** manuscript word and figure checks, construct spelling-variant check, and an account privacy screen in the web UI (`949e5fe`).

### Context and open-access fetch (2026-11-04 to 2026-11-07)

- **Added:** context amend and retire, and a warning when migrations are behind (`744c962`, 2026-11-04).
- **Added:** queued local open-access full-text fetch and evidence extraction workflow (`082c6f2`, 2026-11-05).
- **Changed:** backend updates (`66d81f2`, 2026-11-07).

### Objectives, methodology and research types (2026-11-09 to 2026-11-13)

- **Added:** research objectives, landscape scan runs, methodology advisor, question matching, and research-question critique (`425d3d9`, 2026-11-11).
- **Added:** survey, experimental and mixed-methods research types; evidence synthesis prompt v5 (`425d3d9`).
- **Added:** gap-mining and strategy steps in the web UI (`425d3d9`).
- **Changed:** journey guidance, source verification and overview step wording (`0545e5a`, 2026-11-11).

### Gap suggestions, research plan and screening discussion (2026-11-16)

- **Added:** gap suggestions and research plan (`f1deaaf`, `575efec`).
- **Added:** screening discussion notes and updated screen step in the web UI (`f1deaaf`).

### Responsibilities, literature summary and venue checks (2026-11-19)

- **Changed:** research objectives retire copied context objectives; live objectives feed agent context and exports (`59e2dce`).
- **Added:** reviewer discussion and independent third-reviewer adjudication for screening, with append-only discussion notes and a new review UI (`59e2dce`).
- **Added:** peer-reviewed-only filter; journal candidate ISSNs and venue check (`59e2dce`).
- **Added:** project responsibilities and team UI (`59e2dce`).
- **Added:** literature summary, available as JSON or Markdown (`59e2dce`).
- **Added:** `scripts/start_test_postgres.ps1` for a throwaway test cluster (`59e2dce`).

### Adjudication and design system (2026-11-21)

- **Changed:** screening adjudication keeps both original decisions (`6b942d7`).
- **Added:** full-text open-access fetch queued on a settled full-text include, including an arbiter ruling (`6b942d7`).
- **Added:** design-system theme tokens with scholarly, dense and modern themes, provenance tints, and docs and guard tests (`6b942d7`).
- **Removed:** stale cookies file and old milestone reports (`6b942d7`).
