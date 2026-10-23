# Security & privacy review (plan X.4)

Date: 2026-10-07 · Reviewer: claude · Scope: spec §9 (participant protection, copyright) and §10
(Security: encrypted at rest/in transit, role-based access, secrets vaulted) against the code in this
repository at the time of review. This is a desk review of code and configuration, not a penetration
test; nothing was deployed. Fixes are tracked as plan tasks X.4.1–X.4.10; decisions for the product
owner are Q23 and Q24 in `plan.md` §4.

## Summary

| Area | State | Biggest gap |
|---|---|---|
| Encryption in transit | Partly enforced | LLM endpoint and database connection may be plain text in production |
| Encryption at rest | Not handled by the app | Depends entirely on hosting; nothing states or checks it |
| Secrets | Env vars, `SecretStr`, never logged | No vault; dev compose exposes Postgres with a default password |
| Access control | Strong (per-request role checks, role-matrix test) | Sessions can't be revoked server-side |
| Retention / erasure | Partly built | Account pseudonymisation + unfiled-note scrub + configurable (opt-in) run-snapshot purge exist; no project/file deletion yet, audit log retention still undone (append-only by design) |
| GDPR/DPDP configurability | Absent | No policy settings; third-party processors not listed |

No finding is exploitable without a misconfigured or stolen credential, but the retention/erasure gap
must be closed (or a policy decided) before real users, and certainly before any participant data (M5).

## What is already in place (verified in code)

- **Production settings check** (`backend/app/config.py:76`): refuses to start in production with a
  placeholder/short session secret, SQLite, auto-created schema, a non-HTTPS `PUBLIC_ORIGIN`, missing
  Google OAuth, or an ARC service that is not HTTPS or has a short token.
- **Cookies**: Starlette's signed session cookie, `HttpOnly`, `SameSite=Lax`, `Secure` in production
  (`backend/app/main.py:40-45`). It holds only the user id.
- **Headers** (`backend/app/main.py:77`): strict CSP (`default-src 'self'`, no inline script),
  `X-Frame-Options: DENY`, `nosniff`, referrer and permissions policies. Docs/OpenAPI off in production.
- **Host and size limits**: `TrustedHostMiddleware`; `Content-Length` cap before the body is read.
- **Access control**: every project route resolves the caller's role per request
  (`dependencies.project_access`); `tests/test_role_matrix.py` fails if any project route is missing
  from the matrix. Removing a member takes effect on their next request.
- **Audit log**: append-only via ORM events and database triggers (`backend/app/audit_guard.py`),
  exportable per project (`GET /api/projects/{id}/audit/export`, JSON/CSV).
- **Secrets in code paths**: API keys are `SecretStr`; none of the 24 logging calls in `backend/app` logs
  settings or prompt text (they log ids, counts and exception messages; `logger.exception` also logs a
  traceback). LLM and ARC errors shown to users are fixed strings (`backend/app/agent/llm.py:149-160`,
  `backend/app/agent/arc_client.py:46-69`), so a provider's error body or key can't leak through them.
- **Participant data**: `OpenAICompatibleLLM.for_participant_data()` refuses to send participant data to
  the third-party model unless a local model is configured or the switch is turned off
  (`backend/app/agent/llm.py:91-125`, plan M0.8.4).
- **Untrusted text** is fenced before it reaches a model (M1.2); the BibTeX/RIS and Obsidian exports
  include verified sources only (M1.11.1, X.34.1).

## Findings

Severity: **High** = must be resolved before real users; **Medium** = before production; **Low** =
hardening.

### 1. Encryption in transit

- **1a (Medium) The LLM endpoint may be plain HTTP in production.** `LLM_API_BASE_URL`
  (`config.py:19`) is not checked by `validate_production_settings`, unlike ARC (`config.py:102`).
  Research questions, project context and source excerpts are sent there.
  → **X.4.1**: require `https://` in production; allow `http://` for `LOCAL_LLM_API_BASE_URL` only with
  an explicit opt-in (a local model on a private network is a legitimate setup).
- **1b (Medium) The database connection is not required to use TLS.** Production only rejects SQLite
  (`config.py:86`); a `postgresql://` URL without `sslmode=require`/`verify-full` is accepted, and
  `database.py` sets no TLS options. → **X.4.1**: require `sslmode=require` or stronger in production
  unless the host is a local socket/sidecar (explicit opt-out).
- **1c (Low) No HSTS header.** HTTPS is expected from a terminating proxy, but the app never sends
  `Strict-Transport-Security`, so a first visit over HTTP can be downgraded. → **X.4.2**.
- **1d (Low, note) Forwarded headers.** The image runs `uvicorn --proxy-headers`, which by default trusts
  `X-Forwarded-*` only from 127.0.0.1. Behind a proxy in another container the app sees `http` as the
  scheme. Today nothing depends on it (cookie `Secure` and the OAuth redirect come from settings), but
  anything built later from `request.url` would. Document `FORWARDED_ALLOW_IPS` with X.4.6.

### 2. Encryption at rest

- **2a (Medium, decision) Nothing in the app encrypts data at rest, and nothing says who does.** The
  database (Postgres volume `postgres_data`), object storage (`object_data`, `./data/objects`, M0.10.2)
  and the dev SQLite file are plain files. The spec requires encryption at rest. The practical answer is
  infrastructure encryption (managed Postgres with storage encryption, encrypted volumes or S3 SSE for
  objects) rather than application crypto, but that has to be a stated deployment requirement.
  → **Q24** (ties to Q13 hosting) and **X.4.6** (deployment doc + checklist).

### 3. Secrets

- **3a (Low) No vault.** Secrets come from environment variables / `.env` (pydantic-settings). That is
  acceptable when the platform injects them from its secret manager, but the repo neither documents that
  nor supports `*_FILE` (Docker/Kubernetes secrets). → **X.4.5**.
- **3b (Low, dev only) Local Postgres is published on all interfaces with a fallback password.**
  `docker-compose.yml:7-9`: `POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-change-me}` and `"5432:5432"`.
  On a laptop on shared Wi-Fi this is a reachable database with a guessable password if `.env` is
  missing. → **X.4.4**: bind `127.0.0.1:5432:5432` and fail when the password is unset.
- Rotating `SESSION_SECRET` signs everyone out (acceptable; document in X.4.5).

### 4. Sessions and accounts

- **4a (Medium) Sessions can't be revoked server-side.** The cookie is self-contained and valid for
  Starlette's default 14 days (`max_age` is not set in `main.py:40-45`). Logout only clears the browser's
  copy, so a stolen cookie keeps working until it expires, and there is no "sign out everywhere".
  Project access is still checked per request, so removing someone from a project works; their
  sign-in does not end. → **X.4.3**: a per-user session epoch checked in `session_user`, logout-everywhere,
  and a configurable shorter lifetime.
- **4b (Low, note) First Google sign-in links to an existing account by email**
  (`routers/auth.py:48`). Safe today because Google must assert `email_verified` and development login
  is disabled outside development. If another identity provider is added, linking by email must stay
  limited to verified emails from that provider.
- **4c (Low) No request rate limiting.** Model spend is capped by daily run limits and the token budget
  (M0.9), but other endpoints are unthrottled. Leave to the reverse proxy; note in X.4.6.

### 5. Retention, erasure and data-subject requests

- **5a (High, decision) There is no way to delete a project, a user account, or stored files, and no
  retention period for anything.** Only members and seeds have DELETE routes. Object storage is
  write-once with no delete (deliberately, pending a retention decision — M0.10.2). Research runs keep
  their full `input_snapshot` (`models.py:472`) indefinitely.
  *Status 2026-10-19 (X.4.7.4):* a run-snapshot retention period is now configurable
  (`RESEARCH_RUN_SNAPSHOT_RETENTION_DAYS`, `app/retention.py`, applied by the worker). Default is still
  unset/indefinite — nothing purges until the owner sets a real period. Audit-note retention remains
  undone: `audit_events` is append-only at the DB-trigger level (`app/audit_guard.py`), so no row can be
  redacted in place; a compliant purge needs a separate design (e.g. a retained, re-attributed summary
  written outside the ordinary append-only path), not a setting that silently does nothing.
- **5b (High, decision) Erasure conflicts with the immutable audit log.** `audit_events` is append-only
  (triggers) and records actors by user id plus free-text notes and reasons that may contain personal
  data. GDPR Art. 17 / DPDP s.12 erasure therefore needs a defined approach. The usual one, which keeps
  research integrity: pseudonymise the person (blank email/name on `users`, keep the UUID so the audit
  trail stays intact) and state that audit notes are retained under the research-integrity exemption
  for a fixed period. That was a policy choice for the owner/university. → **Q23**, then **X.4.7**.
  *Status 2026-10-19: Q23 answered (owner via chat, no DPO review — she signs off alone).* The
  pseudonymise-and-keep-audit-trail build is no longer provisional.
  *Status 2026-10-14 (X.4.7.1):* a signed-in person can pseudonymise their own account
  (`POST /api/auth/erase-account`, typing their email to confirm). The email becomes
  `erased-<id>@erased.invalid`, the name and Google identity links are removed, all sessions end, and
  the account can never sign in again; the user id and every audit row are kept. Refused while the
  person is the only owner of a project that has other members.
  *Status 2026-10-19 (X.4.7.5):* the person's private, never-filed notes are now scrubbed on erasure
  too (`app/erasure.py::_scrub_unfiled_notes`): current body/quoted text/source URL blanked, attachments
  deleted from storage, a final blank revision recorded — older `NoteRevision` rows keep their text
  because `note_revisions` is append-only like the audit log (`app/note_guard.py`), so literal deletion
  of a note is not possible. No AI-suggestion text leftover exists to scrub: `AiSuggestion.project_id`
  is `NOT NULL` and `workspace_ai_task.check_request` refuses a note whose `project_id` doesn't match
  the project, so an unfiled note can never carry one. An admin-initiated erasure path remains out of
  scope — there is no platform-admin role today. Audit notes/reasons are still not rewritten
  (append-only, see 5a); project and file deletion are X.4.7.2/.3.
- **5c (Medium) No data export for a person** (access/portability request). The per-project audit
  export exists, but nothing gathers what the platform holds about one user. → **X.4.8**.

### 6. Third parties and configurability

- **6a (Medium) Data sent to third parties isn't listed anywhere a user can see.** The LLM provider
  receives questions, project context and excerpts; scholarly APIs receive search queries and the
  configured contact email (`connectors/crossref.py:139-147`); the ARC service receives the topic. A
  processor register and an in-app notice are needed for GDPR Art. 13/30 / DPDP notice duties.
  → **X.4.9**.
- **6b (Medium) No GDPR/DPDP configuration.** The spec asks for compliance to be "configurable"; there is
  no setting for jurisdiction, retention periods, data residency or consent requirements. Once Q23 is
  answered this becomes a small "privacy profile" (like the discipline profile, M0.7). → **X.4.10**.
- **6c (note) Participant data protection is only half-built.** The participant-data switch exists, but
  there is no participant-data model yet, so nothing classifies data as participant data. Consent
  tracking and anonymisation belong to M5.6/M5.7 and stay there.

### 7. Copyright (spec §9)

- Full text is stored only through the new object store (M0.10.2) and nothing writes to it yet; the
  licence check before storing is part of M2.7.1. No finding today; M2.7 must record the licence basis
  next to each stored object so it can be deleted if a licence is withdrawn (feeds X.4.7).

## Follow-up tasks

| Task | What | Priority |
|---|---|---|
| X.4.1 | Production check: HTTPS LLM endpoints (local model opt-in for HTTP); Postgres TLS (`sslmode`) | P2 |
| X.4.2 | HSTS header in production | P3 |
| X.4.3 | Server-side session revocation (per-user epoch), sign out everywhere, configurable lifetime | P2 |
| X.4.4 | Dev compose: bind Postgres to localhost, no fallback password | P3 |
| X.4.5 | Secrets: `*_FILE` support or documented secret-manager injection; rotation notes | P3 |
| X.4.6 | `docs/DEPLOYMENT.md` security checklist: at-rest encryption, TLS, proxy headers, rate limits | P2 (after Q24) |
| X.4.7 | Retention & erasure: account pseudonymisation, project deletion/archival, object deletion, retention periods | P2 (after Q23) |
| X.4.8 | Personal-data export for a user | P3 |
| X.4.9 | Processor register + in-app privacy notice | P3 |
| X.4.10 | Privacy profile (jurisdiction, retention defaults, residency) | P3 (after Q23) |

## X.4.7.3 project archive and deletion (2026-10-17 design; built 2026-10-20)

Archive is a reversible, owner-only state transition (`app/project_lifecycle.py`,
`POST .../projects/{id}/archive` / `.../unarchive`): `Project.archived_at` is set and
`app/dependencies.py::project_access` refuses every write (409) on the project until it is
unarchived, while reads (including the owner's own `GET .../projects/{id}`) keep working. An
archived project is hidden from the default `GET /api/projects` list (`?include_archived=true`
shows it) but never deleted or rewritten. Restoring is owner-only and audited
(`project.archived` / `project.unarchived`).

**Project-scoped table inventory**, verified 2026-10-20 by enumerating every
`ForeignKey("projects.id")` column in `backend/app/models.py` (the 2026-10-17/2026-10-18 count
below was incomplete):

- **46 direct children** (their own `project_id` foreign key to `projects.id`): `project_members`,
  `gates`, `research_context_items`, `sources`, `screening_criteria`, `screening_decisions`,
  `auto_exclude_audits`, `seed_papers`, `seed_check_runs`, `project_extraction_schemas`,
  `search_queries`, `search_alerts`, `snowball_runs`, `extractions`, `meta_analysis_plans`,
  `constructs`, `theories`, `conceptual_paths`, `claims`, `gaps`, `research_briefs`, `idea_briefs`,
  `dataset_versions`, `analysis_plans`, `analysis_runs`, `robustness_checks`, `exclusion_rules`,
  `qual_excerpts`, `qual_suggestions`, `qual_codes`, `research_questions`, `notes`,
  `ai_suggestions`, `ai_item_locks`, `documents`, `own_paper_lookups`, `provenance_reports`,
  `section_approvals`, `interpretation_findings`, `interpretation_reviews`, `research_runs`,
  `tasks`, `artifacts`, `artifact_edges`, `source_embeddings`, `cluster_runs`. (The earlier list
  omitted `idea_briefs`, `dataset_versions`, `analysis_plans`, `analysis_runs`, `exclusion_rules`,
  `qual_excerpts` and `qual_suggestions`, and miscategorised `conceptual_paths` as indirect-only:
  it has had its own `project_id` foreign key since migration `20261015_0065`.)
- **Indirect children** (no `project_id` column of their own; reached by joining through their
  direct-child parent): `note_revisions` and `note_attachments` (via `notes`), `source_excerpts`
  (via `sources`), `document_versions` (via `documents`), `ai_suggestion_events` (via
  `ai_suggestions`), `claim_evidence` (via `claims`).
- **Project-scoped but deliberately not a foreign key** (so the row outlives a deleted project,
  the same way `llm_usage` outlives an erased user, `app/erasure.py`): `authorship_events` and
  `similarity_checks` carry a plain `project_id` column, not a `ForeignKey`.
- **Stored objects**: source full-text keys and note attachment keys (`ObjectStore`, X.4.7.2).

**Append-only guards** (`app/audit_guard.py`, `app/note_guard.py`, `app/ledger_guard.py`,
`app/suggestion_guard.py`, `app/project_schema_guard.py`) make `audit_events`,
`note_revisions`, `document_versions`, `authorship_events`, `similarity_checks`,
`provenance_reports`, `own_paper_lookups`, `section_approvals`, `interpretation_findings`,
`ai_suggestion_events` and `project_extraction_schemas` reject `UPDATE`/`DELETE` at the database
level. `source_excerpts` is immutable on `content`/`content_hash` only (`app/excerpt_guard.py`)
and can still be deleted; it is not in this list.

**Deletion decision (Q23, 2026-10-19, owner via chat: no DPO review, owner signs off alone):**
refuse the whole hard delete up front, cleanly, naming every append-only table that still holds a
row for the project, rather than build a second, inconsistent tombstone or pseudonymisation path
for projects. `app/project_lifecycle.py::delete_project` checks all eleven append-only tables
above (the ones reachable from a project) before touching anything; if any has a row, it raises
`DeletionBlocked` and nothing is deleted. Because `POST /api/projects` always records a
`project.created` audit event, **every project made through the normal API already has an
`audit_events` row and can never be hard-deleted** -- archiving is the only way to retire a
project with real activity. A project with genuinely no recorded activity (only reachable by
constructing the row directly, which is what the tests do) can be: the 46 direct-child tables are
removed in FK dependency order (`Base.metadata.sorted_tables`, reversed), the five indirect-child
tables are removed by joining through their parent first, every stored object is removed via
`ObjectStore.delete()`, and finally the project row itself. `audit_events` and `llm_usage` are
never part of the cascade (both are designed to survive the project they named). This is the
"refuse deletion of a project that has audit rows, and say so clearly" option named in X.4.7.3's
acceptance criteria, applied to every append-only table a project can reach, not only
`audit_events`.
