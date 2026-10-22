# Owner and developer actions: 2026-10-22

**Written by:** @claude (opus), as part of X.144.

**Source:** this list comes from the SWOT review of 2026-10-22. Each item below needs a person: the owner or a developer. No AI session can close any of them, because each one needs one of these:
- a git action
- a live key
- a running Postgres
- a named human
- the scholar's data
- a licensing decision

AI sessions have already built, tested and recorded everything else in `plan.md`.

## Do these first: they protect the work already done

### 1. Commit the working tree, in reviewed pieces

**What to do:**
- `git status` shows 51 modified files and 21 untracked files. Several sessions made these changes, and none of them is committed.
- This includes two new migrations: `20261022_0089_llm_usage_reservations.py` and `20261022_0090_task_timestamps.py`.
- Commit one task at a time, using the task ids in `plan.md` (rules 42 and 45).
- Use `git add --renormalize .` once, so files with CRLF line endings are stored as LF. `.gitattributes` already sets `eol=lf` (X.66).

**What it unblocks:**
- The test baseline in `plan.md` stops depending on uncommitted work.
- A reset or a clash between two sessions can no longer lose this work.
- Migration numbers 0089 and 0090 become fixed for the second developer (rule 70).

### 2. Run the full suite once on PostgreSQL

**What to do:**
- Set `TEST_POSTGRES_URL` and run `pytest -q -n 0 backend/tests/test_task_queue_postgres.py backend/tests/test_guards_postgres.py`.
- Also run Alembic `upgrade head`, `check`, `downgrade base` and `upgrade head` against a scratch Postgres database. Alternatively, push the branch and read the GitHub Actions run (X.10).

**What it unblocks:**
- It is the only check of `FOR UPDATE SKIP LOCKED`, the append-only triggers and the gate row lock. SQLite cannot exercise any of these.
- X.142 pagination has also not been checked on Postgres.

## Decisions only the owner can make

### 3. PyMuPDF (AGPL-3.0) in the vendored ARC service

**What to do:** choose one of these before the ARC service is hosted for other people:
- accept the AGPL terms
- buy the commercial licence
- switch `backend/arc_retrieval/web/pdf_extractor.py` to `pypdf`

The details are in `docs/security-privacy-review.md` §8. Since X.141, the web image no longer ships PyMuPDF.

**What it unblocks:** any hosted deployment of the ARC service.

### 4. `scholarly`, which scrapes Google Scholar

**What to do:** choose one of these:
- keep it for local use only
- drop the Scholar source and rely on the licensed connectors

The details are in `docs/security-privacy-review.md` §8.

**What it unblocks:** enabling ARC retrieval in any deployment.

## Live checks that no automated test can do

### 5. M2.11.8: one live run against a real model

**What to do:** use the owner's own key, set only in `.env`. Record the model id, the prompt version and the outcome in `milestone-reports/`.

**What it unblocks:** M2.11. It is also the first evidence that the prompts and answer guards work on real model output.

### 6. M2.11.9: live click-through with the owner

**What to do:** go through access control, the audit log and the citation rules in the running app.

**What it unblocks:** M2.11.

### 7. X.51: review by a named person

**What to do:** a person, not an AI session, reviews these areas:
- gates
- access control (`test_role_matrix.py`)
- the citation block
- the AI-exclude audit
- the extraction schema
- the M1 exit

Record one table per area in `milestone-reports/`.

**What it unblocks:** independent sign-off. Today every "independent" review is one AI model checking another.

### 8. X.15: the scholar's own gold set

**What to do:** the scholar labels the topic, the papers and the include/exclude decisions. The Cohen ADHD set is a stand-in, not the scholar's own labels.

**What it unblocks:** M2.9, M3.15 and X.16 (the real-topic dry run).

## Scope

The SWOT review flagged scope growth. More features each add rows to the role matrix and more gate and stage edges to review. Before approving new feature tasks, consider finishing the open P1 items (M2.11, X.16, X.51) first.
