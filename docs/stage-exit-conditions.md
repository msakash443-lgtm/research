# Machine-checkable exit conditions per stage

| | |
|---|---|
| Date | 2026-10-10 (status line updated 2026-10-19) |
| Author | claude (AI session) |
| Status | **Approved, partially implemented.** Owner approved the level rubric 2026-10-11 (Q25): "Required only if it protects evidence, ethics or authorship; convenience/quality is Recommended or Information." Owner also approved (2026-10-12) that an unbuilt Required row reports as Information, not a block, until its task lands — see the note under EC-4.7 and EC-0.4. `app/exit_conditions.py` (X.48.1) implements this document; most per-transition rows are built (see the "Today" column per row). 2026-10-19 (X.72): EC-5.2, EC-5.3, EC-5.5, EC-6.2, EC-6.3, EC-6.4, EC-7.2, EC-13.2, EC-13.3 and EC-15.2 moved from "needs/not built" to real, built checks — see the G4 note below for EC-5.2/EC-5.3's effect on G4. |
| Plan task | `plan.md` **X.45** / **X.53** (this document) · **X.48** (code that implements it) |

## How to read this

A condition belongs to a **transition** — "leave stage A → enter stage B". It is checked when
someone asks to move forward (`POST /stage/advance`) and, for gated stages, shown next to the
gate's approve control on that stage's own screen.

**Levels:**
- **Required** — protects evidence, ethics or authorship. No role, the background worker, or the
  AI can bypass it.
- **Recommended** — a quality condition. A person may proceed by typing a short reason; the reason
  is recorded in the audit log.
- **Information** — a hint only, never blocks anything.

**"Today" column:**
- **built** — computable right now from the named symbol.
- **needs `<task>`** — the stored data this condition needs only exists once that task lands.
- **not built** — no stored data exists for this condition at all. Until its task lands, the row
  is shown as Information "not built yet" — it must never silently report `met: true`.

**The Level column is the level a row has once its task is built.** A row marked "needs `<task>`"
or "not built" is reported as **Information, unmet** (`exit_conditions._not_built`) until then, so
it blocks nothing — even if its Level column says Required. When the task lands, the row switches
to its listed level and starts blocking.

Every unmet row names a one-click fix: the screen and action that resolves it.

**Never locked, at any stage:** quick capture (`POST /api/notes`), reading every screen including
later stages, exploring/asking in exploratory mode, drafting (once built), working on several
steps in parallel, navigating back to any earlier screen, and the owner's re-entry
(`POST /stage/reenter`).

## Global rules (apply to every transition)

| ID | Condition | Level | Machine check | Today | One-click fix |
|---|---|---|---|---|---|
| EC-0.1 | The target stage's gate (if any) is approved by a person | Required | `Gate.status == approved` for the code `gates.GATE_STAGES` maps to the target stage; already enforced in `stage_machine.advance_stage` | built | Open the gate control on this step |
| EC-0.2 | No stale, unreplaced work at or before the target stage | Required | `artifacts.unreplaced_stale(db, project, up_to=target) == []` | built (only research runs and thematic clusters register an `Artifact` today; full coverage needs X.42) | "Redo" list → link to each stale item's own screen |
| EC-0.3 | Earlier gates are approved before this gate can be decided | Required | `gates.pending_earlier_gates(gates, code) == []` (M0.5.10) | built | Link to the earliest pending gate |
| EC-0.4 | The stage belongs to the project's chosen chain | Required | `stage_order.chain(project)` contains the target stage (X.49 now, with one hard-coded `full_journey` chain; per-research-type chains land with X.43) | needs X.49 | — (a configuration error, never overridable) *(owner-reviewed 2026-10-11, Q25: doesn't literally fit "evidence/ethics/authorship" — kept Required because it has no "proceed with a reason" path at all, so Recommended/Information don't fit it either)* |

## Per-transition rules

| ID | Leave → enter | Condition | Level (once built) | Machine check | Today | One-click fix |
|---|---|---|---|---|---|---|
| EC-1.1 | idea → scoped | G1 approved | Required | EC-0.1 with G1 | built | Scope: approve G1 |
| EC-1.2 | idea → scoped | At least one research question or objective written | Recommended | count of `ResearchContextItem.kind in (question, objective)` ≥ 1 | built | Scope: "Add question" |
| EC-1.3 | idea → scoped | Captured ideas not yet promoted | Information | count of `ResearchContextItem.kind == idea` | built | Scope: show ideas |
| EC-2.1 | scoped → search_planned | G2 approved | Required | EC-0.1 with G2 | built | Criteria: approve G2 (on-screen after X.41.2) |
| EC-2.2 | scoped → search_planned | Criteria framework chosen and no criteria problems | Recommended | `criteria.problems(project.criteria_framework, criteria) == []` | built | Criteria: edit criteria |
| EC-2.3 | scoped → search_planned | Known papers (seeds) recorded, for the recall check | Information | count of `SeedPaper` ≥ 1 | built | Criteria & seeds: "Add seed paper" |
| EC-3.1 | search_planned → retrieved | At least one search has run and reported counts | Recommended | `prisma._latest_runs(db, project)` non-empty (promoted to a public `latest_runs` helper in X.48.1) | built | Search: "New search" |
| EC-3.2 | search_planned → retrieved | Every seed paper was found by the searches | Recommended | `recall_report.project_report(db, project)["meets_target"]` is true, or there are no seeds | built | Results check: open the missed-papers list |
| EC-3.3 | search_planned → retrieved | Auto-retrieved sources not yet verified | Information | count of `Source.origin == "retrieved" and not metadata_verified` | built | Sources: filter "unverified" |
| EC-4.1 | retrieved → screened | G3 approved | Required | EC-0.1 with G3 | built | Screening: approve G3 (screen itself waits on M2.3/M2.8) |
| EC-4.2 | retrieved → screened | No unresolved screening conflicts (dual-screen) | Required | for every source in `screening.screenable_sources`, `screening.dual_screen_status(rows)[0] != "conflict"` at both `title_abstract` and `full_text` (only checked when `project.dual_screen_enabled`) | built | Screening: conflict queue |
| EC-4.3 | retrieved → screened | Every record screened at title/abstract | Recommended | every screenable source has `screening.final_decision(rows) is not None` at `title_abstract` (dual-screen: status `agreed`) — message "N records unscreened" | built | Screening: queue filtered "unscreened" |
| EC-4.4 | retrieved → screened | Every record that passed title/abstract has a full-text decision | Recommended | every id in `screening.passed_title_abstract` has a `final_decision` at `full_text` | built | Screening: full-text queue |
| EC-4.5 | retrieved → screened | Sampled AI auto-excludes all audited by a person | Recommended | count of `AutoExcludeAudit.status == "pending"` is 0 | built | Screening: audit sample |
| EC-4.6 | retrieved → screened | Excluded records carry a reason code | Information | already enforced on save by `screening.check_reason`; shown here as a count for transparency | built | — |
| EC-4.7 | retrieved → screened | PRISMA numbers computed from stored decisions | Recommended *(downgraded from Required 2026-10-11, Q25: a reporting-completeness condition, not direct evidence/ethics/authorship protection)* | `prisma.flow_counts` screening/exclusion counts are non-null | needs M2.8 | Results check: PRISMA |
| EC-5.1 | screened → extracted | G4 approved | Required | EC-0.1 with G4 | built | Extraction review: approve G4 |
| EC-5.2 | screened → extracted | Every extracted quote is found word-for-word in the stored full text | Required | every current `Extraction.fields_json`/`evidence_spans` re-checked against the stored source PDF with `extraction_spans.check_evidence_spans`/`FullText.find_quote()` | built | Extraction review: rejected-fields list |
| EC-5.3 | screened → extracted | Every critical field checked side-by-side by a person | Required | every current `Extraction` whose schema marks a field `critical` (`extraction_schema.FieldSpec.critical`) has `verified_by_human == true` | built | Side-by-side review |
| EC-5.4 | screened → extracted | Every included study has an extraction | Recommended | every source whose full-text `final_decision.decision == "include"` has ≥ 1 `Extraction` | built | Extraction: "Start extraction" |
| EC-5.5 | screened → extracted | AI said "not enough evidence" for some fields | Information | count of `null`-valued fields on current `extracted_by == "ai"` extractions | built | Extraction: show fields |
| EC-6.1 | extracted → synthesized | At least one synthesis result exists | Recommended | ≥ 1 `ResearchRun.status == "completed"` or ≥ 1 `ClusterRun` | built | Ask / Themes |
| EC-6.2 | extracted → synthesized | No claim marked verified without evidence | Required | DB trigger + API invariant (`app/claim_guard.py`, `routers/claims.py::verify_claim`); checked here for transparency, always 0 | built | — |
| EC-6.3 | extracted → synthesized | Cited sentences without a verified supporting quote | Recommended | count of `ClaimEvidence` on a research-run claim with `support_confirmed_by IS NULL` | built | Ask: confirm or fix the citation |
| EC-6.4 | extracted → synthesized | AI answers not yet accepted by a person | Information | count of `AiSuggestion` rows with `target_kind == "research_run"` and no `accepted`/`edited_and_accepted` event | built | Ask: suggestion list |
| EC-7.1 | synthesized → gaps_selected | G5 approved | Required | EC-0.1 with G5 | built (gate only) | Gaps: approve G5 |
| EC-7.2 | synthesized → gaps_selected | Every selected gap cites ≥ 2 papers or matrix cells | Required | `gap_miner.validate_gap_evidence` rule, re-checked here for transparency (enforced at creation, `Gap` evidence is immutable after) | built | — |
| EC-8.1 | gaps_selected → framework | G6 approved | Required | EC-0.1 with G6 | built (gate only) | Approvals |
| EC-8.2 | gaps_selected → framework | At least one research question is linked to a selected gap (FINER novelty/relevance) | Required | `ResearchQuestion.gap_links` non-empty for at least one project `ResearchQuestion` (M4.1) | built | Approvals: G6 refuses while unmet |
| EC-8.3 | gaps_selected → framework | At least one conceptual-model path has a recorded measure for every construct it uses | Required | `ConceptualPath` + `Construct.measures` (M4.2) | built | Approvals: G6 refuses while unmet |
| EC-9.1 | framework → design_approved | G7 approved by the owner (ethics; for a no-participants research type, a "no human participants" confirmation) | Required | EC-0.1 with G7 | built (gate only) | Approvals |
| EC-10.1 | design_approved → data_collected | Data collection may only use a local model if participant data is sensitive | Required | M0.8.4 switch + Q4 | not built (M5.1, Q4 open) | — |
| EC-11.1 | data_collected → plan_locked | G8 approved by the owner (analysis plan locked) | Required | EC-0.1 with G8 | built (gate only) | Approvals |
| EC-11.2 | data_collected → plan_locked | Raw dataset stored immutably with a hash | Required | M5.1 | not built | — |
| EC-12.1 | plan_locked → analyzed | G9 approved | Required | EC-0.1 with G9 | built (gate only) | Approvals |
| EC-12.2 | plan_locked → analyzed | Every analysis run executed after the plan was locked | Required | `AnalysisRun.created_at > G8.decided_at` (M5.3) | built | Approvals: G9 refuses while unmet |
| EC-12.3 | plan_locked → analyzed | The findings-to-literature map exists and every finding is confirmed by a person | Required | `interpretation_map.g9_readiness` (M6.9): current `InterpretationFinding` batch non-empty, every row has a `confirmed` `InterpretationReview` | built | Approvals: G9 refuses while unmet |
| EC-13.1 | analyzed → drafted | G10 approved | Required | EC-0.1 with G10 | built (gate only) | Approvals |
| EC-13.2 | analyzed → drafted | Every number in the draft links to an analysis run | Required | `manuscript_guard.section_report` (`number_citation.check_section`) over every saved `Document`, filtered to `unknown_run`/`unresolved_number` | built | Write: link-check |
| EC-13.3 | analyzed → drafted | Every citation in the draft is a verified reference | Required | `manuscript_guard.section_report` (`claim_linker.check_section`) over every saved `Document`, filtered to `unknown_source`/`unverified_citation` | built | Write: link-check |
| EC-14.1 | drafted → revised | Open reviewer comments | Information | M7.1 | not built | — |
| EC-15.1 | revised → submission_ready | G11 approved by the owner | Required | EC-0.1 with G11 | built (gate only) | Approvals |
| EC-15.2 | revised → submission_ready | Zero orphan claims; in-text citations match the reference list | Required | `manuscript_guard.section_report` over every saved `Document`: `orphan_claim_ids` plus `unknown_source`/`unverified_citation`/`evidence_not_cited` findings | built | Write: link-check |
| EC-16.1 | submission_ready → submitted | Submission recorded | Information | M7 | not built | — |
| EC-17.1 | submitted → revision_loop / accepted | None (re-entry is the way back to redo work) | Information | `stage_machine.allowed_next` | built | — |

## Where unmet conditions appear

- The step screen of the stage being left (lock notice from X.47.2).
- The gate control of that stage (Required rows there refuse approval, not just advance — X.48.3).
- `GET /api/projects/{id}/stage/conditions` (X.48.2), machine-readable for any client.

Gate-approve for **G3** and **G4** also refuses while that stage's *reported-as-Required* rows are
unmet (X.48.3). For G3 that includes an unresolved conflict (EC-4.2).

**G4 note:** EC-5.2 (quotes found word-for-word) and EC-5.3 (critical fields checked by a person)
were Information-only (not built) until 2026-10-19 (X.72); both are now real, Required checks and
block G4 approval like any other unmet Required row. A G4 approved before 2026-10-19 under the old
Information behavior was an approval of the process, not of verified extractions; re-open and
re-approve G4 to get the real check's coverage on an older project.
