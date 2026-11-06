# Step-template checklist

One page, used during the X.14.2 walkthrough alongside the three clickable wireframes' own findings table in `README.md`. Where that table asks about the three *not-yet-built* screens (screening queue, evidence table, claim–evidence panel), this checklist is for every screen in the app today — built and not yet built — so the owner can judge the whole journey against the same five checks, plus the one she asked to add.

Added 2026-10-10 at the owner's request (X.40 decision 3), on top of the six checks from `docs/step-by-step-journey-audit.md` §1.

## The step template (owner item A, 2026-10-10)

Every step screen has the same six parts. A screen passes only if all six are present.

| # | Part | Pass when |
|---|---|---|
| 1 | Purpose | One plain sentence at the top: what this step is for. |
| 2 | My work area | The researcher's own inputs/decisions; never written to by the AI. |
| 3 | AI suggestions panel | Titled "AI suggestion, not yet yours"; uses the shared suggestion card (X.46): Accept · Edit then accept · Discard (recoverable via "Show discarded") · Ask again (with guidance) · Why? · Never for this item · Undo; shows model, prompt version, time and who accepted; bulk accept-above / discard-below a confidence on long lists. Hidden when the project's AI setting is Off. |
| 4 | One next-step button | Exactly one primary button saying what to do next. |
| 5 | Back / revisit | "Previous step" link always; owner also sees "Go back to an earlier stage…" (existing re-entry dialog). |
| 6 | Lock / condition message | When a condition applies: its level (Required / Recommended / Information), exactly which condition is missing (from `docs/stage-exit-conditions.md`) and a one-click fix. A pending approval never freezes the screen. A "needs redoing" flag shows when this screen's work is stale. |

### Lock levels
- **Required** — protects evidence, ethics or authorship (unverified citation, approval gates, analysis plan locked before analysis). Nobody can bypass it: no role, not the worker, not the AI. **Approving a gate itself refuses while a Required condition for that stage is unmet, not only moving forward** (owner decision, Q28, 2026-10-11): the refusal names exactly which condition is missing and a one-click fix.
- **Recommended** — quality condition (e.g. "12 records unscreened"). You may proceed with a short reason; the reason goes in the audit log.
- **Information** — a hint only.

An action is greyed out only if it commits something AND the server would refuse it; the tooltip says why.

### Never locked, on any screen, at any stage, for any role that may write (owner decision, Q26, 2026-10-11)
Quick capture · drafting · exploring (Ask in exploratory mode) · reading later steps · working on several steps in parallel · going back. This is **project-wide**, not a per-item setting. The only per-item lock is the AI lock ("never for this item" / "never send this to AI", X.46.3) — unaffected by Q26.

### AI suggestions never
change the researcher's own text · approve a gate · count as a decision until a person acts.

### AI involvement levels (owner decision, Q27, 2026-10-11)
- **Off** — no text is sent to any AI.
- **Suggestions only** (the default, Q14) — AI may comment, challenge, find evidence and check structure, but never supplies manuscript words.
- **Full** — additionally allows reworded sentences the researcher may choose to insert, always labelled in the writing record.

### Gate on its own step
Each gate's approve/reject control appears on the step it belongs to (G1 Scope, G2 Criteria & seeds and Search, G3 Screening, G4 Extraction review, …), as well as on Approvals.

### Screens that fail the template today (from `docs/step-by-step-journey-audit.md` §1)
| Screen | Missing parts | Plan task |
|---|---|---|
| Criteria & seeds | 4 next button, G2 on screen, 6 lock message | X.41.2 |
| Results check | 4 next button, 6 empty/lock state | X.41.3 |
| Sources | 4 next button, 6 stale flag | X.41.4 |
| Ask | 3 suggestion card, 6 "exploratory" label | X.41.5, X.38 |
| All built screens | 3 shared panel, 5 back link, 6 stale flag + condition message | X.41.1, X.41.6, X.41.7 |


## The six checks

1. **What is this screen for?** One sentence, in plain language.
2. **What is the one clear next step?** Is there a single button that says what to do next, or do you have to work it out?
3. **What did the AI propose, and is it waiting for your decision?** If the AI produced something on this screen, can you tell at a glance whether anyone has approved it yet?
4. **How do I approve and move on, or go back?** Is the approve/reject/go-back control right here, or do you have to leave the screen to find it?
5. **Is this screen locked when an earlier step or its approval isn't done yet?** Does the screen itself tell you it's too early, or does it let you try and then refuse?
6. **What is never locked on this screen?** The opposite question, on purpose: name anything on this screen you can always do regardless of approval state (e.g. reading, exporting, adding a private note) — so "locked" and "always open" are both deliberate, not accidental gaps.

## Table (fill in during the walkthrough)

| Screen | 1. What's it for? | 2. Next step? | 3. AI proposal shown? | 4. Approve/go-back here? | 5. Locked when too early? | 6. Never locked | Changes asked for → new plan.md task |
|---|---|---|---|---|---|---|---|
| Overview | | | | | | | |
| Scope | | | | | | | |
| Criteria & seeds | | | | | | | |
| Search | | | | | | | |
| Results check | | | | | | | |
| Sources | | | | | | | |
| Ask | | | | | | | |
| Approvals | | | | | | | |
| Team | | | | | | | |
| Activity | | | | | | | |
| Notes / Capture | | | | | | | |

Outcome per screen: **approved as is** / **approved with changes** (list them) / **redo the screen**.

Walkthrough date: ____ · Scholar: ____ · Note-taker: ____

For the three not-yet-built screens (screening queue, evidence table, claim–evidence panel), use `README.md`'s own findings table instead — those screens don't exist yet, so this checklist's "today" columns don't apply to them; `README.md`'s Q1–Q7 pinned questions already cover checks 1–5 and 6 is implicit in its "where do I go back" question.
