# Step-by-step journey: audit, pre-conditions, data-driven stages, sequencing, demo checklist

| | |
|---|---|
| Date | 2026-10-10 (decisions recorded same day) |
| Author | claude (AI session) |
| Status | **Decisions received from the owner (recorded in `plan.md` §3 Decisions log and below). Still nothing implemented — this remains a docs-only report.** |
| Plan task | `plan.md` **X.40** (docs only); follow-on tasks **X.41–X.44, M2.11, M3.14, M3.15** now added to `plan.md`, unclaimed |

Reviews marked *AI session* below were done by AI assistants (claude, copilot, antigravity, arena sessions). Only reviews marked *named human* count as human review, per `plan.md` rule 11 ("no self-review for P0/P1... a different agent/developer moves `[?]` → `[x]`").

"Stage 2" in this report means **milestones M1 (Literature core) + M2 (Screening)** — the plan has no literal "Stage 2"; its milestones are M0 (closed) through M7. M1 closed in full while the first draft of this report was being written (see §2).

---

## Decisions — owner's answers, recorded 2026-10-10

1. **May M3 (extraction) start before the walkthrough/gold set?** **Yes, conditionally.** M3 backend work may start while the walkthrough (X.14.2) and gold set (X.15) are still pending, on condition that the "red" items close first (the CI-proof reviews, the AI-exclude audit review, the ARC security fix — see §2.1). No screening screen (M2.3) is built before the walkthrough. Extraction/screening quality is never called accepted without the real gold set. Recorded in `plan.md`'s Decisions log; new tasks sequenced in §4 below accordingly.
2. **Gold set (Q10/X.15).** Owner will supply it. Topic, the ~20 relevant papers, the ~50 screened papers and a delivery date are still placeholders — **not filled in yet**, nothing fabricated. `X.15` stays blocked until they arrive.
3. **Wireframe walkthrough (X.14.2).** Owner will do it; date still a placeholder. She asked for one addition to the one-page step checklist: a column **"what is never locked on this screen"**, alongside the originally proposed purpose/next-step/AI-proposal/approve-or-back/locked-state columns. Built as [`docs/wireframes/step-template-checklist.md`](wireframes/step-template-checklist.md) and linked from the wireframes README. `X.14.2` stays blocked until the date and the walkthrough happen.
4. **Data-driven stages (Q22).** **Approved, with two amendments:** (a) starting now, no new code may hard-code the 19-stage order — any code that needs "the next stage" or "every stage" must go through one stage-order helper function; (b) every shipped research type's screen must show its own stage list and which gates apply, never assume the fixed 19-stage list. Both amendments are folded into the design in §3 and into the new task `X.43`.
5. **Which gates are mandatory in every research type?** **G2 (search plan), G3 (screening result), G4 (extraction verification), G7 (design & ethics), G11 (final sign-off).** For a research type with no human participants (a pure literature review), G7 stays mandatory but its *content* becomes a light "no human participants" confirmation instead of a full ethics pack — the gate itself is never removed. A gate is never detached from a stage that stays in a type's chain; a type may only drop an optional gate by dropping its stage.
6. **"Does this source support this sentence" check.** **Approved as proposed** (M3.14): a verbatim-quote anchor, checked by `FullText.find_quote()`, no AI judgement of "is this evidence sufficient" until the screening-accuracy work (M2.9/X.5) gives a way to measure the AI's error rate.
7. **Who approved access control, the audit log and the citation rules?** **Not yet confirmed** — the owner's response left the "yes/no, and name" as a placeholder. Per her own instruction, **no name has been recorded** without a written yes. If and when she confirms, a short live click-through during the Stage-2 demo (§6) will stand in for the missing click-through evidence.
8. **AI research answers — their own approval, or stay "exploratory"?** **Neither of my two options — a third, better one.** The owner's decision: AI research answers get the **same generic "AI suggestion card" as every other AI output** in the app — Accept / Edit then accept / Discard / Ask again — not a one-off special-case approval button. Until a person accepts an answer it stays labelled "Exploratory: check the sources yourself". **Finding while implementing this decision: no such reusable suggestion-card component exists anywhere in the app today** — checked directly: no screen consumes the already-built synonym-suggestions endpoint (M1.5.3), and the screening AI rationale (M2.2.1) is only ever shown as plain text in the not-yet-built screening wireframe, never as an actionable card. `X.38` is now "build that shared component for the first time, applied first to research-run answers" — recorded in `plan.md`.
9. Still open, no answer yet: Q4 (local LLM required?), Q13 (who holds hosting accounts), Q14–Q21 (the researcher-workspace design questions), Q23 (data retention), Q24 (encryption confirmation) — full list in §3.3.

---

## 1. What the audit found: is the app really "one step at a time"?

**In short:** every screen explains what it's for. Most don't have a clear "what's next" button, and only two screens (Scope, Search) let you approve anything in place — everything else sends you to a separate Approvals page. Nothing in the screen list is actually locked: you can click into any step at any time, even ones that don't make sense yet. The server still blocks the underlying actions (a search won't actually run before you approve the search plan), so nothing unsafe happens — but the *screen* doesn't say so until you've already tried.

The five checks, one line each:
- **(a)** Does the screen say what it's for?
- **(b)** Is there one clear "do this next" button?
- **(c)** Does it show what the AI proposed and whether a person has approved it?
- **(d)** Can you approve-and-move-on, or go back, right there?
- **(e)** Is the screen itself locked/disabled when an earlier step or its approval isn't done yet?

### Screen-by-screen table

|Screen|a|b|c|d|e|Evidence|Smallest fix|
|---|---|---|---|---|---|---|---|
|Overview|PASS|PASS|PARTIAL|PARTIAL|N/A|Purpose text and the "next action" card come from `web/js/workflow.js` (`GUIDES`, `nextAction`) rendered in `web/js/steps/overview.js:14-29`. The "Waiting on you" box only lists pending gate codes (`overview.js:38-48`), not what the AI itself produced. The only button goes forward (`overview.js:23-24`); there's no "go back" here.|Add a short list of AI proposals waiting for review (unverified retrieved sources, unapproved research answers, pending AI screening suggestions) — the data is already loaded for this page.|
|Scope|PASS|PASS|N/A (nothing AI-written on this screen)|PARTIAL|N/A (first step)|"Move to Scoped" button and the G1 approve/reject controls both appear (`web/js/steps/scope.js`). No "go back" control on this screen itself.|Add a small "Go back to Idea" link (owner only) that opens the existing re-entry dialog.|
|Criteria & seeds|PASS|FAIL|N/A|FAIL|PARTIAL|Only a status line, "Editable until G2 is approved" (`web/js/steps/criteria.js:145-147`); no approve button and no "go to Search" button live on this screen — you must leave it to approve G2 elsewhere. The edit button does correctly disappear once locked (`criteria.js:151-155`).|Add the G2 approve/reject control and a "Next: Search" button directly on this screen.|
|Search|PASS|PASS|PARTIAL|PASS|FAIL|"New search" and the G2 approve/reject controls are both present (`web/js/steps/search.js`). But the "New search" button is always clickable, even before the scope/search-plan approval; the server correctly queues the search as blocked, but the screen doesn't warn you first.|Grey out "New search" with a tooltip ("waits for G2") instead of letting you submit and then find out.|
|Results check|PASS|FAIL|N/A (numbers, not AI text)|FAIL|FAIL|Shows PRISMA counts and known-item recall only (`web/js/steps/results.js`); no next-step button, no approval control, no locked/empty state.|Add a "Next: Sources" button once at least one search has produced results.|
|Sources|PASS|FAIL|PASS|PARTIAL|N/A|Every AI-retrieved source clearly shows "not yet approved" vs "approved by [name]" (`web/js/steps/sources.js:161-172` uses the shared provenance badge — the best example of check (c) in the app). "I've checked it" is the approve action; there's no next-step button to Ask.|Add a "Next: Ask" button once sources exist.|
|Ask|PASS|FAIL|PASS|FAIL (by design)|FAIL|Honestly labelled: "Not approved by a person. Research answers have no approval step yet; check the cited sources." (`web/js/steps/ask.js:40-43`). There is genuinely no approve/reject control for an answer yet, and the question box is open at every stage, even before screening is done.|**Decided (X.38):** add the shared AI-suggestion card (Accept / Edit then accept / Discard / Ask again), label "Exploratory: check the sources yourself" until accepted. No such card exists yet anywhere in the app — this is a new shared component, not a copy of an existing one.|
|Approvals|PASS|PASS|N/A (this is the human-decision screen, not an AI output screen)|PASS|N/A (it is the gate screen itself)|The only screen with the full 19-step progress list, the only place that shows "this work is now stale and needs to be redone", and the only place with a "go back to an earlier stage" button.|None needed here — the problem is that *only* this screen has these controls.|
|Team|PASS|N/A|N/A|N/A|N/A|Member management only; not part of the step sequence.|None.|
|Activity|PASS|N/A|N/A|N/A|N/A|Read-only audit log, already shows actor, model and prompt version per entry (`web/js/steps/activity.js`).|None.|
|Notes / Capture|N/A|N/A|N/A|N/A|N/A|A quick-capture popover in the top bar, not a step in the sequence. The bigger "researcher workspace" redesign (capture → inbox → writing editor) is fully designed in `docs/researcher-workspace-design.md` but **not built**, and is explicitly marked "design only, not approved for implementation" pending your answers to Q14–Q21.|None until X.31.6 walkthrough happens.|
|Screening|—|—|—|—|—|**Correctly not built.** It isn't in the app's step list at all. Only a static, no-server-calls prototype exists at `docs/wireframes/screening-queue.html`, waiting for your walkthrough (X.14.2) before anyone is allowed to build the real thing (plan.md rule 32: "no screen in M2+ is built before its wireframe has been walked through by the scholar").|Not a gap — this is the rule working as intended.|

### Where the app breaks the one-step-at-a-time pattern

1. **Nothing is actually locked at the screen level.** You can click into any of the ten screens, in any order, at any time — the left-hand list and the web address both let you jump straight to "Sources" before you've even run a search. This doesn't let you do anything unsafe (the server still refuses the underlying action), but it doesn't *guide* you either. The only real lock today is on the Criteria screen's edit button, which disappears once the search plan is approved.
2. **The screen list isn't one screen per stage.** There are 19 research stages behind the scenes, but only 10 screens exist, grouped as Plan / Gather / Synthesize / Project. Everything from "Retrieved" onward is shown only as plain text in a "later stages" list — you can't click into it.
3. **Approve-and-move-on lives almost entirely on one screen.** Only Scope and Search let you approve something right where you're working; everywhere else you have to go to the separate Approvals screen. "Go back to an earlier stage" exists only on Approvals.
4. **"This is now out of date" only shows in two places** — the Approvals screen and individual AI-answer cards on the Ask screen. If a search or a screening decision becomes stale after you go back a stage, nothing tells you on the Search or Sources screens.
5. **Screens that break the pattern most:** Criteria & seeds, Results check, Sources, and Ask.

### Fix, now approved (X.41, X.38)
**X.41** (step-pattern fixes) and **X.38** (the shared AI-suggestion card) are both approved and added to `plan.md`, not yet built. X.41 would: add a clear next-step button to every screen that's missing one; add the matching approve/reject control to the screen it belongs on, not just Approvals; show a small "this needs redoing" flag on any screen with stale work; and grey out actions that the server will refuse anyway, so you see the lock *before* you try, not after — but its exact step-to-approval mapping waits for your walkthrough answers on `docs/wireframes/step-template-checklist.md` (added at your request, with its extra "what is never locked" column), per X.41's own dependency on X.14.2. X.38 would build, for the first time, one reusable "AI suggestion card" (Accept / Edit then accept / Discard / Ask again) and apply it first to research-run answers on the Ask screen, replacing today's plain "not approved" text.


---

## 2. Pre-conditions: what has to close before Stage 2 (M1 + M2) is really done

**Note on timing:** while this report was being written, another session closed the whole of milestone M1 (Literature core) — all 14 epics, including the citation-block subtasks and the gold-set recall report. The table below reflects that: M1 is now fully closed, and the remaining open "red"/"amber" work in Stage 2 is entirely inside M2 (Screening). One inconsistency survived the closure and is still worth fixing: `M1.1.13` (ARC fetch hardening) is still marked `[?]` on its own line even though its parent `M1.1` is marked closed with "M1.1.13 ... independently reviewed 2026-10-10, no defect" in the closing note — a reviewer should flip that line's own status to match.

### 2.1 Open items in M2, and their colour

**Red = must close before more backend work on extraction (M3) starts. Amber = waiting on you, can run in parallel.**

|ID|Status|What's missing|Colour|
|---|---|---|---|
|M2.5, M2.5.1, M2.5.2 (AI-exclude audit & agreement guard)|Built, not yet committed to the shared repo or reviewed|Needs commit + human review|**Red**|
|M2.6 (AI prioritisation of what to screen first)|In review|Lower priority (P3)|Not red|
|M2.7.5, M2.7.6 (full-text fetch hardening)|Not started|Low priority (P3/P2)|Not red|
|M2.8, M2.8.1 (screening approval + PRISMA numbers)|Not started|Needed before screening can be approved at all|**Red**|
|M2.3, M2.3.1, M2.3.2 (Screening screen)|Not started|Waiting on your wireframe walkthrough (X.14.2)|**Amber**|
|M2.9 (screening accuracy check against your gold set)|Not started|Needs your gold set|**Amber**|
|M2.10 (M2 exit check)|Not started|Depends on M2.8|**Red**|

### 2.2 Who actually reviewed what

No in-review item in M2 has been checked off by a **named human**, and the same is true of everything that was just closed in M1: every recorded review — including the batch that closed all of M1 today — reads "independent reviewer session", which is another AI assistant, not a person, or names an AI coding tool (copilot, antigravity). The only recorded human involvement in Stage 2 is: your sign-off (recorded only as "user", in chat, not a click-through) on the access-control rules, the audit-log rules and the citation-blocking rules, plus your confirmation of how the gold-set recall number should be interpreted. Closing M1 did not add any new named-human review — it added more AI-session review on top of the same chat sign-offs.

- **Gates (approve/reject buttons, who may click them):** built and only AI-reviewed. You approved the underlying rules in chat.
- **Access control (who can see/edit/approve what):** you approved the rules in chat; the detailed test coverage has only been AI-reviewed.
- **Audit log (who-did-what trail):** you approved the rules in chat; its database-level tamper protection has never actually been tested against a real production-style database (see §2.3).
- **Citation block (blocking made-up references):** you approved this on 2026-10-10, and M1's citation-verifier and provenance-UI epics (M1.10, M1.13) are now both marked closed with that same approval cited on their own lines — the earlier mismatch between the plan's header note and the X.30 line (described in the appendix below) is the one piece still worth a human fixing.

### 2.3 Is the automated testing actually running on a real database?

**Proven, with evidence:** yes, on GitHub, repeatedly. The project's automated check ("CI") runs on every push to the shared codebase at `github.com/msakash443-lgtm/research`, using a real Postgres database (the same database engine used in production), not just the lightweight stand-in used for day-to-day development. The most recent run (2026-10-10, 14:48 UTC) passed: 2,170 checks passed, 5 intentionally skipped, then the database upgrade/downgrade/upgrade cycle and three worker-concurrency checks all passed against real Postgres. The five most recent runs on the shared codebase all passed.

**Still unproven:**
- A batch of newer work sitting only on this machine (the AI-exclude audit guard, the active-learning prioritisation, the shared circuit-breaker fix, and four new database changes) has **never** been run through that GitHub check or against real Postgres — only against the lightweight stand-in, here. A full local run of the whole test suite today (with that extra work included) passed: 2,336 checks passed, 6 skipped, nothing failed — but that was still on the lightweight stand-in, not Postgres.
- The database rules that make the audit log and researcher's notes "impossible to secretly edit" are created by the database setup scripts but their actual tamper-blocking behaviour has never been tested against real Postgres — only reasoned about.
- The rule that stops two people approving things out of order has never been tested under real concurrent load on Postgres.
- Nothing in the automated check calls a real AI model or a real external scholarly database — every test uses a stand-in that returns pre-written fake answers. This is correct practice for automated testing, but it means "the AI synthesis actually produces a useful answer against a live model" is not something any automated check proves.

### 2.4 Everything waiting on a named human decision

|ID|Question|Blocks|Status|
|---|---|---|---|
|X.30|Sign-off on access control, audit log, citation block|—|Closed, but recorded only as "user, in chat" — see decision 7|
|X.14.2|Walk through the three clickable wireframes and say what needs to change|Screening screen (M2.3)|**Waiting on you**|
|X.15 / Q10|Supply your gold-set topic, ~20 known-relevant papers, ~50 hand-screened papers|Screening accuracy check (M2.9), every stage demo|**Waiting on you**|
|Q4|Is a local/self-hosted AI model *required* for sensitive data, not just offered as an option?|Data-collection and analysis work later in the project|Open|
|Q13|Who holds the hosting accounts and in which region — you or the development team?|Formal sign-off that the project is run in your name|Open|
|Q22|Should the sequence of stages be configurable per research type, or stay as one fixed 19-step chain?|The workspace-redesign stage indicator|Open — see §4 for a full design|
|X.31.6 + Q14–Q21|Walk through the researcher-workspace wireframes and answer: default AI involvement (Q14), how voice notes are transcribed (Q15), who can see a note before it's filed (Q16), whether you have a plagiarism-check licence (Q17), approval to add a Word-export library (Q18), wording for the AI-use statement (Q19), whether provenance timestamps need independent certification (Q20), how long to keep every autosave (Q21)|The whole researcher-workspace feature (capture, writing editor, provenance report)|**Waiting on you**|
|X.38|Should an AI research answer get its own approve button, or stay "exploratory" until screening is approved?|The Ask screen's approval behaviour|Open — see decision 8|
|Q23|Data retention / right-to-erasure policy, needs university data-protection sign-off|Not yet scoped|Open|
|Q24|Confirm encryption-at-rest, tied to the hosting decision (Q13)|Not yet scoped|Open|

### 2.5 Proposed order to close these

1. You commit and push the work currently sitting only on this machine, so the GitHub check proves it on real Postgres.
2. A named human reviews: the AI-exclude audit guard (M2.5/M2.5.1/M2.5.2) and the `M1.1.13` status-line fix.
3. Add the missing Postgres behaviour tests (proposed X.44) and the missing "mark as stale when you go back" coverage (proposed X.42).
4. Build the screening approval + PRISMA numbers screen (M2.8).
5. Close the M2 exit check (M2.10).
6. In parallel with the above: your wireframe walkthrough and your gold set, leading to the Stage-2 demo (§6).


---

## 3. Design: letting different kinds of research skip or reorder steps (Q22) — **approved 2026-10-10, with two amendments**

**In short:** today every project follows the exact same 19-step chain, hard-coded in the software. This design lets each project pick a "research type" — a named, shorter or reordered list of steps — while five human-approval checkpoints can never be skipped, whatever type is chosen, because they protect against fabricated evidence, unethical data collection, or an unapproved final submission. Tracked as `plan.md` **X.43**.

**The owner's two amendments, folded in below:** (a) from now on, no new code may hard-code the 19-stage order — any code that needs "the next stage" or "every stage" goes through one stage-order helper function, so X.43's eventual rewrite has one call site to change, not many; (b) every shipped research type's screen must show its own stage list and which gates apply, never assume the fixed 19-stage list is universal.

- This is a **separate choice from your discipline** (economics, psychology, etc., which already exists and only changes citation style and preferred databases). Research type is a second, independent setting.
- A **literature review** project (no data collection of its own) and a **qualitative study** project (interviews, themes, no statistical plan) are both expressed as shorter versions of the same 19-stage list, with some stages skipped and labels adjusted — see the two example chains below.
- **Every existing project keeps today's full 19-step chain automatically.** Nothing about an existing project changes; this only offers a choice to *new* projects (or, with your approval, to a project still at its very first stage).
- **Five approval checkpoints are mandatory in every research type, with no exceptions:** approving the search plan and screening criteria before bulk retrieval (G2); approving the final included set of sources (G3); verifying extracted data is correct (G4); approving the research design and ethics (G7); and the final sign-off before submission (G11). **For a research type with no human participants** (a pure literature review), G7 stays mandatory but its *content* becomes a light "no human participants" confirmation instead of a full ethics pack — the gate is never removed, only its checklist shortened. The optional checkpoints (idea scoping G1, selecting research gaps G5, approving the research-question framework G6, locking the analysis plan G8, approving the interpretation of results G9, approving each draft section G10) can be skipped only by skipping the stage they belong to — a checkpoint is never silently detached from a stage that stays in the chain.
- **Example — Literature review** (no data of its own): Idea → Scope approval (G1) → **Design & ethics approval** (G7, light "no human participants" version, moved earlier since there's no primary data collection to design) → Search-plan approval (G2) → Retrieved → Screening approval (G3) → Extraction-verification approval (G4) → Synthesis → Gap selection (G5) → Draft (G10) → Revise → Final sign-off (G11) → Submitted → Revision → Accepted. This drops the framework/hypothesis stage, the data-collection stage, and the "lock the analysis plan" / "approve the results interpretation" stages.
- **Example — Qualitative study:** the same full chain, but without "lock the analysis plan" (G8 — there's no pre-registered statistics to lock), and with three stages relabelled for a qualitative audience: the framework stage becomes "Sensitising concepts & questions", the design stage becomes "Design, interview guide & ethics", and the analysis stage becomes "Themes & interpretation". Going back and forth between data collection and analysis (common in qualitative work) uses the project's existing "go back a stage" feature — nothing new is needed for that loop.
- **What proving this works would look like:** automated tests confirming every mandatory checkpoint is present in every shipped research type; confirming a literature-review project correctly skips the dropped stages while still requiring its checkpoints in the right order; confirming that going back a stage only resets the checkpoints that exist in that project's chosen chain; confirming an existing project's behaviour is completely unchanged; confirming no other part of the code still hard-codes the 19-stage order (amendment a); and a role-permission test for every new screen/button.
- **Size estimate: Large** (touches the stage-progression logic, the approval logic, the task queue, three API endpoints, and the web screens that currently assume the fixed 19-step list).
- **Main risks:** the order in which checkpoints may be approved/reopened needs care when the chain is shorter; the screens need to stop assuming a fixed stage list (today they do, and the amendment makes fixing this mandatory, not optional); and nothing may ever be allowed to register work against a stage that was dropped from a project's chosen chain — the design requires that to fail loudly and visibly rather than silently.
- **Sequencing:** built after `M3.11` (claim/evidence tables), per the owner's decision 4 — it doesn't block anything today, and M3.11's tables are referenced by nothing in this design, so there's no technical reason to build it earlier; the ordering is the owner's preference for where to spend effort first.


---

## 4. Order of work for the next stages — approved 2026-10-10, now in `plan.md`

All of the tasks below (X.41–X.44, M2.11, M3.14, M3.15) are now in `plan.md`, unclaimed. X.38 (the shared AI-suggestion card, decision 8) is also updated in `plan.md` with its decided design. None are built yet.


### Phase A — finish Stage 2 (M1 + M2), red items first

M1 closed in full while this report was being written (see §2 note); Phase A below covers only what's left, all inside M2.

|ID|Title|Priority|Depends on|Size|
|---|---|---|---|---|
|(your action)|Commit and push the work sitting on this machine, so it's proven on Postgres|P1|—|Small|
|—|Named-human review: AI-exclude audit guard (M2.5/M2.5.1/M2.5.2), the `M1.1.13` status-line fix|P1|push|Small|
|X.44 (new)|Add Postgres behaviour tests: prove the audit log, excerpt and note tamper-protection actually refuses edits on real Postgres; prove the approval-ordering protection under real concurrent load|P1|CI proof|Medium|
|X.42 (new)|Make "go back a stage marks later work stale" actually cover searches, criteria, screening decisions and verified extractions — today it only covers AI research answers and thematic clusters|P1|—|Medium|
|X.41 (new)|The screen fixes from §1 (next-step buttons, approve-in-place, stale flags, pre-emptive locking)|P1|Your wireframe walkthrough answers|Medium|
|X.38|Build the shared "AI suggestion card" (Accept / Edit then accept / Discard / Ask again), apply to research-run answers first|P2|—|Medium|
|M2.10|M2 exit check|P1|everything above|Small|
|M2.3|Screening screen|P1|Your wireframe walkthrough (X.14.2) — **waiting on you**|Large|
|M2.9|Screening-accuracy check against your gold set|P2|Your gold set (X.15) — **waiting on you**|Medium|
|M2.11 (new)|**Stage-2 demo** on your real topic, using your gold set (see §5)|P1|everything above|Small|


### Phase B — verified extraction (starts once Phase A's red items are closed)

|ID|Title|Priority|Depends on|Size|
|---|---|---|---|---|
|—|Named-human review of the extraction-schema and extraction-model work already built|P1|—|Small|
|M3.3|AI pulls data only from the full text already stored for a source — never from the AI's general training knowledge — and says "not enough evidence" rather than guessing when a field can't be found|P1|M3.2, M2.7.3 (done)|Large|
|M3.4|Every quote the AI used as evidence is checked, word-for-word, against the actual stored document. A quote that doesn't actually appear can't be marked verified.|P0|M3.3|Medium|
|M3.11|Rebuild the "claim" and "evidence" tables (removed earlier as unused) with the rule: a claim can never be marked verified unless it has at least one evidence row|P0/P1|M3.2, citation block|Medium|
|M3.14 (new)|The "does this source really support this sentence" check, described below|P1|M3.4, M3.11|Medium|
|M3.6|A person checks the AI's extracted numbers side-by-side with the original PDF; the extraction-verification approval requires every critical field to be checked|P1|M3.4|Large|
|X.43|Data-driven stages (§3, approved with amendments)|P2|M3.11 done|Large|
|M3.15 (new)|**Extraction-stage demo** on your real topic and gold set|P1|everything above|Small|

**On the "does this source support this sentence" check (decision 6, approved):** right now, the system only checks that a cited source *exists and has been verified as a real, correctly-attributed reference* — it never checks that the source's content actually backs up the sentence it's attached to. The fix (M3.14, approved and in `plan.md`) adds a check that every cited sentence carries an actual quote from that source, and that quote is confirmed to appear word-for-word in the stored document — the same mechanical check used for extraction. If no matching quote exists, the citation is shown as "support not checked" and a person has to confirm it. An AI judgement of "does this evidence really support this claim" stays **deliberately deferred**, because there's no way yet to measure how often the AI would be wrong, and a wrong "yes, supported" would be worse than no check at all — revisit once the screening-accuracy work (M2.9/X.5) gives a way to measure it.

**Rules that shape this order, restated plainly:** no new stage's screens are built while the current stage still has open red items; verified, evidence-checked extraction is built before more screens or data analysis are added; the screening screen is never built before your wireframe walkthrough; every stage of work ends with a live demo on your real topic using your real gold set, not a synthetic example.

---

## 5. Demo checklist for the end of Stage 2

To be run live with you, on your real topic, using your gold set, once Phase A above is closed.

|#|Claim being tested|How to test it|Expected result|Status today|Evidence|Pass / Fail|
|---|---|---|---|---|---|---|
|1|**A fabricated citation is blocked.** An AI answer can never cite a made-up reference.|Add a source with an invented title and a fake DOI. Run the automatic check — it should come back "not verified". Ask a question that would cite it.|The whole answer is refused, not just flagged; the audit log records why.|Built; proven only in automated testing with a stand-in AI, never against a live AI model|`backend/tests/test_citation_guard.py`|☐|
|2|**A gate can't be bypassed by any role, or by the background worker.**|Try to queue a bulk search before the search plan is approved. Have a co-author or reviewer try to approve it (should be refused). Have a supervisor try to approve a sign-off that's owner-only (should be refused). Then have the owner approve it and confirm the work actually runs.|Every wrong attempt is refused; only the right role, after approval, makes it proceed.|Built and automatically tested; the "two people approving out of order" edge case has never been tested on a real production-style database|`backend/tests/test_gated_tasks.py`, `test_role_matrix.py`|☐|
|3|**The audit log shows who did what, with the AI model and prompt version used.**|Run an AI research question; open the Activity screen.|The log entry names the actor, the action, the AI model, and which version of the instructions it was given.|Built and working today|`web/js/steps/activity.js`|☐|
|4|**Changing an earlier stage marks later work as out of date.**|Approve several stages, produce a research answer, then go back to an earlier stage.|The answer is flagged "stale"; the system lists exactly what needs redoing; later approvals are reset and must be re-approved.|**Built only for AI research answers and theme clusters.** Searches, criteria, screening decisions and verified extractions are *not yet* flagged stale when you go back — this is the X.42 gap above.|`backend/tests/test_reentry.py`, `test_stale_stage_guard.py`|☐|
|5|**Every number in a draft traces back to a computed result.**|N/A yet — drafting and statistical analysis haven't been built.|—|**Not built.** Honest status: this can't be demonstrated yet.|—|☐ (record as "not built")|
|6|**The researcher's own written words are never silently overwritten — by anyone, including the AI.**|Edit a note; check the history. Check that nothing in the AI's code ever writes to a researcher's note text.|The original wording is always recoverable; a dedicated search of the AI-related code found no code path that writes to a researcher's own note text.|Built for notes and for source excerpts; the full writing editor isn't built yet, so this is proven only for the pieces that exist today|`backend/app/note_guard.py`, `backend/app/excerpt_guard.py`|☐|

---

## Technical appendix (for developers)

### Stage and gate model today
- 19 stages, in order, defined in `backend/app/models.py` (`class ProjectStage`): `idea, scoped(G1), search_planned(G2), retrieved, screened(G3), extracted(G4), synthesized, gaps_selected(G5), framework(G6), design_approved(G7), data_collected, plan_locked(G8), analyzed(G9), drafted(G10), revised, submission_ready(G11), submitted, revision_loop, accepted`.
- Stage → gate mapping lives in `GATE_STAGES` in `backend/app/gates.py`.
- Approver roles (`GATE_APPROVER_ROLES`, `backend/app/gates.py`): G1, G2, G3, G4, G5, G6, G9, G10 need owner-or-supervisor; **G7, G8, G11 need the owner specifically** — these are the commitments that lock the study or go out under the authors' names.
- Forward movement: `stage_machine.advance_stage()` — refuses to skip stages, refuses if the target's gate isn't approved, refuses if any earlier stale work is unresolved.
- Backward movement ("re-entry"): `artifacts.reenter_stage()`, owner-only. Marks every later-stage result stale (and anything that depends on it, even transitively), resets every gate for a later stage back to pending.
- "Stale, unreplaced work blocks progress" logic: `artifacts.unreplaced_stale()`.
- Today, only two kinds of work register themselves as trackable results that can go stale: AI research-run answers (`backend/app/agent/executor.py`, the `register_artifact(...)` call near the end of a successful run) and thematic clusters (`backend/app/thematic_clusters.py`). Searches, criteria, screening decisions and extractions do **not** yet register — this is the gap behind X.42 and demo row 4.

### Citation verification today
- `backend/app/citation_verifier.py`: checks that a cited source's title, authors, year and DOI genuinely match a real bibliographic record (via Crossref/Semantic Scholar/OpenAlex, or a person's own "I've checked it"). It does **not** compare the source's content against the sentence it's cited for — this is the gap M3.14 addresses.
- The hard block itself (an answer citing an unverified source fails the whole run, rather than being saved with a warning) lives in `backend/app/agent/executor.py` and is tested in `backend/tests/test_citation_guard.py`.

### Extraction and evidence today
- `backend/app/extraction_schema.py` defines the field schema and requires every filled-in field to carry a `{quote, page, section}` evidence pointer — but only checks that pointer is *shaped* correctly, not that the quote is real.
- `backend/app/fulltext.py`'s `FullText.find_quote()` is the existing word-for-word quote-matching engine (already handles line breaks, hyphenation, and PDF-layout noise) that the still-to-be-built verbatim-span check (M3.4) and the proposed support check (M3.14) would both call.
- The `Claim`/`ClaimEvidence` tables that would enforce "no claim without evidence" were deliberately removed earlier as dead code and have not yet been rebuilt (M3.11).

### Audit and provenance today
- `AuditEvent` rows carry actor, action, a payload, and — when the action was an AI output — `model_id` and `prompt_version`. Confirmed call sites: `backend/app/agent/executor.py`, `backend/app/prescreen.py`.
- `backend/app/audit_guard.py`, `backend/app/excerpt_guard.py`, `backend/app/note_guard.py` all enforce "you can never edit or delete this row" at both the application layer and (via database triggers, created in migrations) the database layer — but, per §2.3, the database-trigger behaviour has not yet been tested against real Postgres.

### Continuous-integration evidence
- Workflow file: `.github/workflows/ci.yml`. Runs on every push/pull-request to the shared `main` branch, using a real `pgvector/pgvector:pg16` Postgres service container.
- Latest observed successful run: commit `86509ad`, run `38061081230`, 2026-10-10 14:48 UTC — `https://github.com/msakash443-lgtm/research/actions/runs/38061081230`. Main suite (SQLite): 2,170 passed, 5 skipped. Then: `alembic upgrade head` / `alembic check` / `alembic downgrade base` / `alembic upgrade head` against real Postgres, all clean, followed by `backend/tests/test_task_queue_postgres.py` — 3 passed. The preceding four runs on `main` also succeeded.
- What this does **not** prove: the full test suite only runs on the lightweight SQLite stand-in, not Postgres; no live AI model or live external scholarly API is called by any automated test (`fake_llm` / mocked HTTP throughout); the ARC retrieval microservice's own tests are not yet wired into this workflow (tracked separately as X.1.2).

### Inconsistencies found in `plan.md` while researching this report (left for the file's other editors to fix, per the "edit only your own lines" rule)
- `M1.1.13`'s own task line is still marked "in review" even though its parent `M1.1` was closed today with a note saying "M1.1.13 ... independently reviewed 2026-10-10, no defect" — the subtask line itself was never flipped to match.
- `M2.4.1`'s own task line (the dual-screen feature) names its migration as `20261010_0039`, but the migration actually present in the repository under that number is `20261010_0039_query_alerts.py` (M1.12's work); the dual-screen migration that actually exists is `20261010_0040_dual_screen.py`.


### Evidence base for this report
File/line references throughout this report were read directly from the repository on 2026-10-10 and are accurate to that snapshot; `plan.md` in particular is being actively edited by other sessions and line numbers there may have already shifted. Everything stated as fact above was read from the repository, a real test run, or the live GitHub Actions history — nothing was inferred or assumed.
