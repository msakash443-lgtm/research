# Milestone report — M<n> <name> — <final | interim> — <YYYY-MM-DD>

> Copy this file to `M<n>-<final|interim>-<date>.md`. Fill every section; write "none" rather
> than deleting a section. Facts come from `plan.md`, `docs/traceability.md`, the test run and the
> Decisions log — no claims without evidence.

| Field | Value |
|---|---|
| Milestone | M<n> — <name> |
| Spec §12 exit criterion | <quote the exit criterion> |
| Exit criterion met? | **yes / no** — evidence: <test name, demo recording, report> |
| Related §13 acceptance criteria | <13.x, or "none for this milestone"> |
| Prepared by / date | <who> / <date> |

## 1. Features built (against `docs/traceability.md`)
| Spec | Requirement | Tasks | Status | Evidence (test / screen) |
|---|---|---|---|---|
| | | | | |

Rows in scope for this milestone that are **not** done: <list, or "none">.

## 2. Test results
**Automated:** `pytest -q` from the repo root → **<N> passed, <F> failed** (previous report: <N>).
Untested paths (e.g. Postgres-only SQL): <list>.

**Gold set** (X.15; spec §11) — "not measured" is an allowed answer; a guess is not.
| Metric | Target | Result | Sample |
|---|---|---|---|
| Search recall (known items) | ≥ 95% | | |
| Screening AI–human κ | ≥ 0.8 | | |
| False-exclusion rate (audited) | < 2% | | |
| Extraction accuracy, critical fields | ≥ 90% | | |
| Citation verification before export | 100% | | |

## 3. Non-negotiables demo check
| # | Check | Result (pass / fail / not built) | Evidence |
|---|---|---|---|
| N1 | Every citation clicks through to a verified source; unverifiable is blocked | | |
| N2 | Every AI output shows producer, model, date, approval status | | |
| N3 | The 11 gates block the next step until approved | | |
| N4 | Changing an earlier stage marks downstream work stale | | |
| N5 | Every number in a draft links to an analysis run | | |

## 4. Known issues and deliberate deferrals
| Item | Type (bug / gap / deferred) | Task | Why |
|---|---|---|---|
| | | | |

## 5. Deviations from the spec
From the `plan.md` Decisions log and task notes since the last report.
| Date | Deviation | Spec § | Reason | Spec updated? |
|---|---|---|---|---|
| | | | | |

## 6. Next demo
Date: <≤ 2 weeks out> · Scope: <tasks> · Scholar actions needed: <reviews, answers to open Qs>.
