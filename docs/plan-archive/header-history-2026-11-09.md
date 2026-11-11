# Header history, 2026-11-09

Test baseline row replaced by X.178 (PostgreSQL-only test suite). Verbatim previous text:

| Test baseline | repo root → `pytest -q -n0 -o addopts=""` → **3731 passed, 13 skipped, 0 failed, 29 setup errors** (2026-11-04, after merging local X.161–X.169 work with origin/main and dropping X.167; was 3765 on 2026-10-29 before the merge). The 29 errors are `WinError 6`/`50` TestClient fixture errors in `test_cors.py`, `test_deviation_log_api.py`, `test_journal_tables_api.py`, `test_robustness_suite.py`; all 32 tests in those files pass when run alone (12 + 20). The pass count fell because `test_search_promote.py` was deleted with X.167 (count not verified). `node --test backend/app/web/js/test/*.mjs` → **223 passed, 0 failed** (2026-11-04; was 218). SQLite/offline for the pytest run. Earlier counts: `docs/plan-archive/header-history-2026-10-23.md`. |
