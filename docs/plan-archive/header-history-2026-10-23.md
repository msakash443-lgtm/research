# Header history 2026-10-23

Superseded header text moved verbatim from `plan.md` on 2026-10-23 (rule 17a).

**Updated by (2026-10-22):** @claude (opus) 2026-10-22 — SWOT follow-up builds X.141 (requirements split), X.142 (opt-in pagination), X.143 (rate limits), X.144 (process hygiene), X.145 (logging); all `[?]`, need a non-Opus reviewer; X.146 added, gated on a committed tree; owner checklist `milestone-reports/owner-actions-2026-10-22.md`. History (newest last, one line each, rule 17a): `docs/plan-archive/header-history-2026-10-22.md`, `header-history-2026-10-21.md`.

**Test baseline (2026-10-22):** repo root → `pytest -q -n 4` → **3600 passed, 13 skipped, 0 failed** (@claude (opus) X.145 build; previous 3597; includes other sessions' uncommitted work in the tree). `node --test backend/app/web/js/test/*.mjs` → **206 passed, 0 failed** (2026-10-22, @claude (opus) X.145 build; previous 197). SQLite/offline only; Postgres/live connectors/GitHub Actions not run.

---

## Superseded header rows 2026-10-23 (before the 2026-10-23 trim)

**Updated by (2026-10-23):** @claude (haiku) 2026-10-23 — X.122 code fix (codebook reuse without definition), X.4.7.3 doc count (53 direct children), M5.2 page cap (`MAX_PAGES` 100); all `[?]` for non-builder review; M5.5 status unchanged. History: `docs/plan-archive/header-history-2026-10-23.md`, `header-history-2026-10-22.md`.

**Current focus (2026-10-23):** M0 and M1 closed; M2–M6 in progress. **Needs review or a fix:** X.4.0 (§7 and indirect-table count), X.4.9 (waits on X.99 review), X.10 (pushed CI run), X.2 (DEPLOYMENT.md lines), X.95 (P2: blocked `localStorage` stops app load). **Build next:** unblocked M-tasks. **Truly blocked (off main path):** Q4 model name (participant-data stages M4.5/M5.x), X.15/M2.9/M3.15 optional quality measurement. Owner decisions on X.12, X.13, X.29, X.45, X.53, X.55, X.79, X.34.3, X.68, M2.11.6, Q8, Q13 are recorded in Decisions; do not re-ask. **Blocking:** X.73 (P0) blocks every browser-smoke-pending task. **Review:** `[?]` tasks awaiting a non-Opus reviewer; M2.13.1 needs a live licensed-API check.

**Test baseline (2026-10-23):** repo root → `pytest -q -n0 -o addopts=""` → **3652 passed, 13 skipped, 0 failed** (2026-10-23, @claude (haiku) X.122/X.4.7.3/M5.2; serial run, earlier counts used `-n 4`, previous 3600). `node --test backend/app/web/js/test/*.mjs` → not re-run 2026-10-23 (no JS changed; last 206 passed, 2026-10-22). SQLite/offline only; Postgres/live connectors/GitHub Actions not run. Earlier counts: `docs/plan-archive/header-history-2026-10-23.md`.
