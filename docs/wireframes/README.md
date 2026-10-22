# X.14 clickable wireframes

Static, clickable prototypes for the three M2+ screens that plan.md rule 32 says must be walked through by the scholar **before** they are built. Open `index.html` in a browser (double-click works; no server, no build). Data is invented and nothing calls the app's API. The screening queue keeps its example decisions in this browser only so its reload walkthrough can be demonstrated; the other prototypes reset on reload.

See also [`step-template-checklist.md`](step-template-checklist.md): a one-page checklist for the walkthrough of **every** screen in the app today, built and not-yet-built, added 2026-10-10 at the owner's request (X.40 decision 3).


| File | Screen | Gates the build of | Spec |
|---|---|---|---|
| `screening-queue.html` | Keyboard-first title/abstract screening, AI suggestion + rationale, bulk actions, AI-exclude audit sample, dual-screen conflict, G3 | M2.3 (also shows M2.4, M2.5, M2.8 UI) | §5.3, §8 G3, §10 Usability |
| `evidence-table.html` | Papers × fields matrix, cell states, side-by-side quote on its page, confirm/correct/not-reported, CSV/Excel export, G4 | M3.5 (also M3.6.1, M3.6.2) | §5.4.3–5.4.4, §8 G4, §10 Usability |
| `claim-evidence-panel.html` | Sentence → evidence panel, verified-only citation picker, section checks, export block, per-section G10 with AI-involvement level | M6.2 (also M6.4, M6.11) | §5.11.3, §5.11 gate/QC, §8 G10 |
| `workspace-writing.html` | Researcher workspace: editor, Sources/AI/Record panel, paste resolver, similarity check, AI level, writing-record report (design doc S4–S6) | Walkthrough X.31.6 (screens S4–S6 are already built; S1–S3, S7 walkthrough was waived 2026-10-14) | design doc A5 |
| `research-type-picker.html` | Scope screen: choose the research type (owner, at Idea, before any gate), see the stage list and mandatory gates | X.43.9 (unlocked state walked through by the owner 2026-10-22); locked state answered 2026-10-22 (Q3: other types greyed out with the reason, X.43.10) | §3 Q22, X.43 decision 4b |
| `observability-dashboard.html` | Observability dashboard: step counts and timings, model calls with tokens and lower-bound cost, failure counts (no error text), pinned Q1–Q5. Drafted 2026-10-22, not yet walked through | X.7.3 (built only after the walkthrough, rule 32) | X.7.0–X.7.2 |
| `index.html` | Start page, walkthrough tasks | — | — |
| `wireframe.css`, `wireframe.js` | Shared look (the app's colour tokens) and the question-pin toggle | — | — |

What the wireframes assume, taken from what exists today (so the scholar can disagree with it):

- Screening: decisions are append-only and Undo adds a row (M2.1.3); the AI suggestion is never a decision and low confidence becomes "maybe" (M2.2.2–M2.2.3); exclusions need an `E` code from the project's criteria (M2.1.2); G3 locks screening (M2.1.3). Approvers are owner or supervisor (`APPROVE_ROLES`).
- Evidence table: fields and the three critical (★) fields come from `backend/app/extraction_schemas/default.json` (M3.1.1). A value needs a quote found word for word in the paper (M3.4); "not reported" is the AI abstaining (M3.3.2).
- Claim–evidence: citations only to verified sources; unverified ones block export rather than warn (spec principle 2, N1, M6.4).

Not decided by these wireframes (each is a pinned question on its screen): whether the AI suggestion shows before or after the person decides; whether bulk exclude exists; matrix orientation; export columns; whether the claim–evidence panel is the same panel as the X.31 workspace "Sources" tab (design doc S4); whether "This is my argument, not a factual claim" is an acceptable way past the orphan-claim check.

Also a choice the wireframe made that the scholar should confirm: a critical (★) cell marked "Not reported" does **not** hold up G4 (there is nothing to check). The alternative is that a person must confirm every "Not reported" on a critical field too.

## Findings (fill in during the walkthrough, then copy into the milestone report)

Walkthrough date (evidence table): 2026-10-15 · Scholar: the owner (also the developer) · Note-taker: Claude

| Screen | Where do I approve? (found it? how long?) | Where do I see the source? | How do I go back? | Answers to the pinned questions | Changes asked for → new plan.md task |
|---|---|---|---|---|---|
| Screening queue | | | | Q1–Q7: | |
| Evidence table | Yes, found "Ask for G4 approval" without help | Quote on its page is not enough alone: also wants a link to the original source | Back to table, Esc and browser Back all work; place kept | Q1: supervisor can check cells as well as approve. Q2: add a link to the original source. Q3: works. Q4: states are clear. Q5: toggle rows/columns. Q6: exports include quotes, page numbers and a status column per field. Q7: not too strict. Critical (★) "Not reported": a person must confirm it before G4 opens. | See plan.md X.63 (supervisor cell check, source link, orientation toggle, richer export, critical Not-reported confirmation). Outcome: **approved with changes** |
| Claim–evidence panel | Not recorded separately | Not recorded separately | Not recorded separately | Q1–Q6: not answered one by one; the owner approved the screen as is, so the wireframe's stated assumptions stand. | None. Outcome: **approved as is** (2026-10-15) |
| Workspace writing (S4–S6) | | | | W1: the UI can be changed later (owner, 2026-10-15; taken to mean the status amount stands for now). W2: keep as is (Sources tab default; one panel with Sources/AI/Record, which also settles claim-evidence Q6). W3: origin colours ON by default. W4: acceptable (highlight stays until answered, nothing blocks saving). W5: "suggestions only" (the level stays as designed; hint wording not commented on). W6: the report includes only the sections the researcher chooses (owner, 2026-10-15). | W3 → plan.md X.31.23; W6 → plan.md X.31.24. Outcome: **approved with changes** (2026-10-15) |

Walkthrough, research-type picker (X.43.9): done 2026-10-22 by the owner (the scholar; Note-taker: Claude). Before it, a mechanical check ran (not a walkthrough): the three types list with 19 / 15 / 18 stages, choosing Qualitative shows 18 stages, Save works, and the supervisor view is read-only. The wireframe cannot show the locked state (a gate approved), so the scholar cannot see that screen yet.

| Screen | Where do I change the type? (found it? how long?) | Where do I see which gates apply? | How do I go back? | Answers to the pinned questions | Changes asked for → plan.md task |
|---|---|---|---|---|---|
| Research type picker | Owner at Scope, "View as" Owner; seen, accepted | Stage list shows every stage and marks G2, G3, G4, G7, G11 as required in every type; accepted | Not recorded | Q1: placement on the Scope screen accepted. Q2: show every stage with its gate (the owner answered "all"; read as accepting the full list, not only gated stages). Q3: not answered; the locked state was not shown in the wireframe. | None for the two accepted points. Q3 and the locked state stay open. |

Scholar: the owner | Date: 2026-10-22 | Note-taker: Claude | Outcome: **approved as is** for the two points walked through; the locked state and Q3 are still open (outcome to be confirmed when they are shown)

Walkthrough, observability dashboard (X.7.3): answers recorded 2026-10-22 from the owner's chat answers (Note-taker: Claude). Wireframe: `observability-dashboard.html`.

| Screen | Where do I see the totals? | Where do I see the cost? | How do I go back? | Answers to the pinned questions | Changes asked for → plan.md task |
|---|---|---|---|---|---|
| Observability dashboard | Top tiles and tables on one page; not yet tested by the owner | Model calls table, with a lower-bound total | Not recorded | Second round, owner's answers 2026-10-22: Q1: whole project history; the time window is scoped as follow-up X.137 (the owner's answer was "scope it"; read as a follow-up, not built now). Q2: queued, running and paused shown as three columns (X.135 changes the endpoint). Q3: cost total as a lower bound, yes. Q4: owner only; co-authors, reviewers and supervisors do not see the page (X.136 changes the endpoint). Q5: yes, each failure links to its details; error text stays hidden. | Applied to the wireframe 2026-10-22: Waiting split into three columns; the view is owner only; time-window note added; each failure has an "Open" link. Tile counts corrected to 11 failed (sums now match the step table). Outcome: **approved with changes**, confirmed by the owner in chat 2026-10-22. |

Outcome per screen: **approved as is** / **approved with changes** (list them) / **redo the wireframe**. Rule 32 is met for a screen only when it is approved (with or without changes).
