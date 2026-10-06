# Human sign-off — access control, audit log, citation block

**Author:** @claude (code and AI reviews). **Needed:** a named person, not an agent (plan.md X.30). AI reviewers already approved most of these tasks (plan.md X.27); this is the human check on top.
**Status:** access control and audit log tasks are `[x]` after AI review; citation-block tasks M1.10.3–M1.10.5 and M1.13.1 are `[?]`. Nothing is committed (user rule: no commits or pushes).
**Baseline:** `pytest -q` from `research-main/research/` with the Python 3.12 venv → 1123 passed, 3 skipped on 2026-10-05 (the 3 are Postgres-only tests; CI runs them, see X.1.1).

All code paths are under `research-main/research/backend/`. Record your result under X.30 in `plan.md` (see the last section).

## How to review

1. Start the app locally (README "Run locally"). In development you can sign in as any email with the local development account, which makes multi-user checks easy: use one browser profile (or private window) per person.
2. Work through the three areas below. Each has the files to read, what to try in the app, and what should happen.
3. Anything that surprises you is worth writing down, even if it turns out to be intended.

## Access control (M0.3.1–M0.3.4)

- **Files:** `app/dependencies.py` (`project_access`, role sets), `app/routers/members.py`, `app/gates.py` (`GATE_APPROVER_ROLES`), `tests/test_role_matrix.py` (every project route × 7 kinds of user).
- **Rules as built:**
  - Read anything in a project: every member (owner, co-author, supervisor, reviewer).
  - Write (context, sources, verify a source, start runs, searches): owner and co-author.
  - Manage (invite/remove members, discipline profile, send a project back a stage): owner only.
  - Gates: owner or supervisor for G1–G6, G9, G10; owner only for G7, G8, G11. Co-authors and reviewers can never decide a gate.
  - A non-member gets "not found" (404), so they can't even learn the project exists; a member with the wrong role gets 403.
- **Try:**
  1. As the owner, create a project and invite three people who have each signed in once: a co-author, a supervisor, a reviewer.
  2. As the reviewer: you can see the project, but adding a source fails.
  3. As someone not invited: the project doesn't appear and its URL gives "not found".
  4. As the co-author: try to approve gate G2 (refused). As the supervisor: approve G2 (works). As the owner: try to remove yourself when you are the only owner (refused).
- **Decide:** are these the right people for each action? In particular, should a supervisor be able to approve G1–G6 on their own, and is owner-only re-entry (`stage/reenter`, task M0.5.5) right?

## Audit log (M0.4.1–M0.4.5)

- **Files:** `app/audit.py` (`record`), `app/audit_guard.py` (refuses changes and deletes), migrations `20261002_0005` and `20261003_0006`, `app/routers/audit.py` (list and export).
- **Rules as built:**
  - Every change writes an event in the same database transaction as the change, so you can't get one without the other.
  - Events can't be edited or deleted through the app, and database triggers refuse `UPDATE`/`DELETE` (Postgres also refuses `TRUNCATE`). A database administrator can still drop the trigger; that limit is documented.
  - Every member, including reviewers, can read and export the trail (decided 2026-10-03, Q12).
  - Events record who acted (a person's id, or `agent:<name>` for automated work) and, for AI output, the model and prompt version. They don't copy source text.
- **Try:**
  1. Do a few things in a project (add a source, verify it, approve a gate, start a run).
  2. Open `GET /api/projects/<id>/audit/export?format=csv` and check each action is there, with your account as the actor and the time.
  3. Open the CSV in a spreadsheet: a value starting with `=` is shown as text (prefixed with `'`), not run as a formula.
  4. Optional, technical: in the dev SQLite database, run `UPDATE audit_events SET action='x';`. It must be refused.
- **Decide:** is anything you did missing from the trail, or recorded under the wrong person?

## Citation block (M1.10.1–M1.10.5, M1.13.1)

- **Files:** `app/citation_verifier.py` (checks title, authors, year and DOI against Crossref/Semantic Scholar/OpenAlex), `app/source_verification.py` (stores the result; only a match sets "verified"), `app/agent/executor.py` (`_citation_problem`, the block), `app/prompts/evidence_synthesis.v3.toml` (tells the model to cite only verified sources), `app/web/app.js` (`verificationText`, `verificationActions` on source cards), `tests/test_citation_guard.py`.
- **Rules as built:**
  - A source counts as verified only after a person clicks "I've checked it" or an automatic check finds a matching scholarly record.
  - The model is told which sources it may cite. If its answer still cites an unverified source, or a source number the run doesn't have, the run fails, nothing is saved, and the audit log records `research_run.citation_rejected`.
  - An automatic check that can't find a paper says "not found". It doesn't call it fabricated, because one service missing a paper doesn't prove it doesn't exist.
- **Try** (needs a model configured, or the fake endpoint used in the 2026-10-05 demo):
  1. Add a made-up reference with an excerpt. Don't verify it. Ask a question: the answer must not cite it (the model either leaves it uncited or says it needs verifying); if it does cite it, the run fails with "cited an unverified source".
  2. Add a real reference, click "I've checked it", ask again: the answer may cite it and the run completes.
  3. With lookup connectors enabled (`CONNECTORS_ENABLED=["crossref"]`, plus `CONNECTOR_CONTACT_EMAIL`), click "Check automatically" on a real reference and on a made-up one, run the worker, and look at the "Last automatic check" line on each card.

## Known, accepted behaviour (decided 2026-10-05)

A person's "I've checked it" is final. If a person verifies a source with a made-up but well-formed DOI, it counts as verified and can be cited. A later automatic check that disagrees (`mismatch`) does not undo it. What you get instead: the source card shows "Verified by a person" together with "Last automatic check: Mismatch", and the audit log names who verified it. If you think this should change, say so in your sign-off.

## How to record sign-off

In `plan.md`, under **X.30**, add a line with your name, the date, and for each area either "approved" or the problems you found. Change X.30 to `[x]` only when all three areas are approved. Agents must not fill this in.
