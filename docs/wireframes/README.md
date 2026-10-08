# X.14 clickable wireframes

Static, clickable prototypes for the three M2+ screens that plan.md rule 32 says must be walked through by the scholar **before** they are built. Open `index.html` in a browser (double-click works; no server, no build). Data is invented, nothing is saved, nothing calls the app's API.

| File | Screen | Gates the build of | Spec |
|---|---|---|---|
| `screening-queue.html` | Keyboard-first title/abstract screening, AI suggestion + rationale, bulk actions, AI-exclude audit sample, dual-screen conflict, G3 | M2.3 (also shows M2.4, M2.5, M2.8 UI) | §5.3, §8 G3, §10 Usability |
| `evidence-table.html` | Papers × fields matrix, cell states, side-by-side quote on its page, confirm/correct/not-reported, CSV/Excel export, G4 | M3.5 (also M3.6.1, M3.6.2) | §5.4.3–5.4.4, §8 G4, §10 Usability |
| `claim-evidence-panel.html` | Sentence → evidence panel, verified-only citation picker, section checks, export block, per-section G10 with AI-involvement level | M6.2 (also M6.4, M6.11) | §5.11.3, §5.11 gate/QC, §8 G10 |
| `index.html` | Start page, walkthrough tasks | — | — |
| `wireframe.css`, `wireframe.js` | Shared look (the app's colour tokens) and the question-pin toggle | — | — |

What the wireframes assume, taken from what exists today (so the scholar can disagree with it):

- Screening: decisions are append-only and Undo adds a row (M2.1.3); the AI suggestion is never a decision and low confidence becomes "maybe" (M2.2.2–M2.2.3); exclusions need an `E` code from the project's criteria (M2.1.2); G3 locks screening (M2.1.3). Approvers are owner or supervisor (`APPROVE_ROLES`).
- Evidence table: fields and the three critical (★) fields come from `backend/app/extraction_schemas/default.json` (M3.1.1). A value needs a quote found word for word in the paper (M3.4); "not reported" is the AI abstaining (M3.3.2).
- Claim–evidence: citations only to verified sources; unverified ones block export rather than warn (spec principle 2, N1, M6.4).

Not decided by these wireframes (each is a pinned question on its screen): whether the AI suggestion shows before or after the person decides; whether bulk exclude exists; matrix orientation; export columns; whether the claim–evidence panel is the same panel as the X.31 workspace "Sources" tab (design doc S4); whether "This is my argument, not a factual claim" is an acceptable way past the orphan-claim check.

Also a choice the wireframe made that the scholar should confirm: a critical (★) cell marked "Not reported" does **not** hold up G4 (there is nothing to check). The alternative is that a person must confirm every "Not reported" on a critical field too.

## Findings (fill in during the walkthrough, then copy into the milestone report)

Walkthrough date: ____ · Scholar: ____ · Note-taker: ____

| Screen | Where do I approve? (found it? how long?) | Where do I see the source? | How do I go back? | Answers to the pinned questions | Changes asked for → new plan.md task |
|---|---|---|---|---|---|
| Screening queue | | | | Q1–Q7: | |
| Evidence table | | | | Q1–Q7: | |
| Claim–evidence panel | | | | Q1–Q6: | |

Outcome per screen: **approved as is** / **approved with changes** (list them) / **redo the wireframe**. Rule 32 is met for a screen only when it is approved (with or without changes).
