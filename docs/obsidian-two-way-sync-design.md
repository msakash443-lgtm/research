# Obsidian two-way sync / import — design (X.34.3)

Status: **design only, blocked before any build subtask is created** — see "Blocking
prerequisite" below. No code in this change.

## 1. Scope

In scope: `ResearchContextItem` rows whose kind a researcher edits in their own words —
`question`, `objective`, `hypothesis`, `methodology`, `variable`, `decision`, `idea`, `gap`,
`claim`, `memo` (every kind the exporter already writes to `Context/*.md`, X.34.1).

**Out of scope, permanently** (not a phase-2 extension — these rows are not the researcher's own
words, or an inbound edit to them would let untrusted/external text decide something that must
only ever be decided by a verified record or a person):

- `Source` notes. Verification status, DOI/metadata and the abstract are retrieved/verified
  records; an edited `Sources/*.md` file in the vault must never change `metadata_verified`,
  citation text, or any other field. Sources stay export-only.
- `ResearchRun` notes. The answer is the model's output with its own provenance
  (`model_id`/`prompt_version`/`input_snapshot`); it cannot be "edited back" without breaking that
  provenance chain. Runs stay export-only.
- Gates, stage, roles, audit rows — nothing inbound from a vault can approve a gate, advance a
  stage, or change membership, directly or indirectly (research-integrity rule 1).

## 2. Stable note ids

The exporter (X.34.1) already writes `id: "<uuid>"` into every note's frontmatter
(`obsidian_export.py::_context_note`/`_source_note`/`_run_note`) — that id *is* the
`ResearchContextItem.id` (or `Source.id`/`ResearchRun.id`) at export time. Import reuses it as the
match key, never the file path or title (Obsidian renames/moves files freely; vault
folders are reorganised by the user without the app's knowledge).

- A vault note **with** a `research-main` `id:` frontmatter key that resolves to a context item
  still in this project → an **update candidate** for that item.
- A vault note **with** an `id:` that doesn't resolve (wrong project, deleted item, or an id from
  a different export entirely) → rejected, reported as `unknown_id`, never silently adopted under
  a new id (a copy/paste between vaults must not merge two unrelated items).
- A vault note **without** an `id:` (created fresh inside Obsidian, or copied from a non-exported
  template) → a **new-item candidate**. It is never auto-created as a `ResearchContextItem`: it is
  staged (see §5) for the researcher to accept into a specific kind, exactly like a promoted `Note`
  (X.31.2) — an id is minted only on acceptance, at which point the vault file would need its
  frontmatter rewritten with that id for the *next* round-trip to recognise it (the sync response
  returns the new id/frontmatter block for the researcher's own editor to apply; the server does
  not reach into the vault to rewrite it — see §6, this is file-upload territory already).

## 3. Change detection

Reuse the `Note`/`NoteRevision` append-only shape (`backend/app/models.py:1022-1065`), not a
diff/CRDT: a new `ObsidianImportRevision` row per accepted inbound change, `content_hash` is
`sha256` of the normalised (newline-collapsed, trailing-whitespace-stripped) note body.

Per sync run, for every update candidate (§2):

1. Compute `incoming_hash` from the vault note's body (frontmatter excluded — frontmatter is
   metadata, not content).
2. Compare against `ObsidianSyncState.last_synced_hash` (one row per `ResearchContextItem`,
   written on every successful export *and* every successful import so both directions share one
   watermark) and the item's **current** `content_hash` (computed fresh from
   `ResearchContextItem.content` right now, not cached — the server row may have changed through
   the normal app UI since the last sync, which has no sync-state write of its own today and must
   not need one retrofitted onto every write path just for this feature).
3. `incoming_hash == last_synced_hash` → vault side unchanged; nothing to do, no audit event (not
   "every inbound note", only every inbound *change*, matching the task wording).
4. `incoming_hash != last_synced_hash and current_hash == last_synced_hash` → clean inbound
   update: write it (see §5), advance `last_synced_hash`.
5. `incoming_hash != last_synced_hash and current_hash != last_synced_hash` → both sides changed
   since the last sync → **conflict** (§4), regardless of whether `incoming_hash == current_hash`
   (identical edits made independently still need the watermark advanced, but as a conflict record
   with `resolution: identical`, not a silent merge — the researcher should know it happened).

## 4. Conflict rules

No automatic merge, ever (there is no reliable word-level merge for prose, and a silent merge is
exactly the kind of "proceeds past a human decision" the research-integrity rules forbid for every
other inbound-text path). A conflict:

- Is **never applied**. The server-side content is untouched.
- Is recorded as a `ObsidianImportConflict` row: item id, `last_synced_hash`, `current_hash`,
  `incoming_hash`, the incoming text (fenced as untrusted, §6), timestamp.
- Produces exactly one audit event (`project.obsidian_import_conflict`) with ids/counts only, per
  research-integrity rule 5 — no text in the payload.
- Surfaces in the UI (follow-on build task, not this design) as a pending decision: keep server
  text / take vault text / edit a third version — a person decides, the server never guesses.
- Does **not** block the rest of the sync run; unrelated clean updates (§3.4) still apply.

## 5. Writing an accepted inbound change

Mirrors `NoteRevision`: a new table `ObsidianImportRevision` (`item_id`, `revision` int,
`body`, `imported_by`, `created_at`) — append-only, enforced the same way `note_guard` enforces
`NoteRevision` (no UPDATE/DELETE trigger). `ResearchContextItem.content` is updated to the new
text (it has no revision history today — unlike `Note`, so this is also the first time a context
item's content becomes editable at all; that's a deliberate, narrow carve-out: only through this
audited, conflict-checked path, never a plain `PUT`).

## 6. Untrusted text

The inbound body is the researcher's own prior words, re-exported through X.34.1's neutralisation
and (if nothing tampered with the file) unchanged — but the server has no way to prove that: the
file travelled through a third-party app and the filesystem since export. It gets the same
treatment as every other inbound text source (research-integrity rule 3):

- Run through `clean_untrusted` (invisible-character stripping, length cap) before it ever reaches
  `ResearchContextItem.content` or a prompt.
- Never parsed as instructions, never auto-sent to an LLM, never used to set `metadata_verified`,
  `verified_by_human`, a gate decision, or `ProjectStage` — stated explicitly because `content` for
  kind `decision` reads like a decision but is *the researcher's record of* a decision, not gate
  approval itself; nothing in this design lets an imported `decision` item satisfy G-anything.

## 7. Audit

One `project.obsidian_imported` event per sync run (not per note — matches the existing
`project.exported` shape), payload: `{format: "obsidian", updated: n, conflicts: n, rejected_unknown_id: n,
new_candidates: n}` — ids/counts/statuses only, never the text, per rule 5.

## 8. Blocking prerequisite — file uploads

Every mechanism above assumes the server receives vault file content from the researcher. The
only way to do that today would be a new `POST /projects/{id}/import/obsidian` endpoint accepting
uploaded files — but per `AGENTS.md`/`CLAUDE.md`, the current app is explicitly scoped as "no
arbitrary web scraping, code execution, or **file uploads** yet". That boundary was set
deliberately (alongside "no arbitrary web scraping" and ARC's HTTP-only isolation) and isn't this
task's to lift unilaterally — it also opens new attack surface this design hasn't addressed at all
(upload size limits, zip-bomb/zip-slip handling reusing X.34.1's path-safety code, antivirus/
content-type checks, who may upload — presumably `WRITE_ROLES` matching export's `READ_ROLES`
being too permissive for a write path).

**This design is implementable once file uploads are in scope; it is not implementable before
that.** X.34.3 stays open, not built, pending that scope decision.
