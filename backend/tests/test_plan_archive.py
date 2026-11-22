import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "plan_archive.py"
_spec = importlib.util.spec_from_file_location("plan_archive", SCRIPT)
plan_archive = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(plan_archive)

SAMPLE = """## 0. Legend
- [~] **M<milestone>.<task>** example
## 2. Tasks
- [x] **M1.1** Finished work — `P1` — owner: somebody
  first note
  second note
- [x] **M1.2** Parent — `P2`
  - [ ] **M1.2.1** Open child
- [x] **M1.3** Old — done; detail: `docs/plan-archive/tasks-done.md`
  old note
- [~] **M1.4** Claimed
  claimed note
- [?] **M1.5** Review
  review note
- [!] **M1.6** Blocked
  blocked note
## 3. Decisions
## 4. Blockers
| ID | Question | Raised by | Status |
|---|---|---|---|
| Q1 | One | A | **answered** yes |
| ~~Q2~~ | Two | A | **answered** yes |
| Q3 | Three | A | **partly answered** yes |
| Q4 | Four | A | **answered for X** yes |
| Q5 | Five | A | open — **answered** later |
## 4a. Other
## 5. Work log
| Date | Who | Tasks | Outcome |
|---|---|---|---|

| 2026-01-01 | A | one | a |
| 2026-01-02 | A | two | b |
| 2026-01-03 | A | three | c |
| 2026-01-04 | A | four | d |
"""


def test_archive_stubs_a_done_task_and_moves_its_notes_verbatim():
    result = plan_archive.archive(SAMPLE)
    assert "- [x] **M1.1** Finished work — `P1` — done; " + plan_archive.STUB_MARK in result.plan_text
    assert result.task_blocks[0] == "- [x] **M1.1** Finished work — `P1` — owner: somebody\n  first note\n  second note"


def test_archive_leaves_open_child_tasks_in_place_under_a_stubbed_parent():
    result = plan_archive.archive(SAMPLE)
    assert "- [x] **M1.2** Parent — `P2` — done; " + plan_archive.STUB_MARK + "\n  - [ ] **M1.2.1** Open child" in result.plan_text


def test_archive_moves_notes_from_an_existing_stub_and_keeps_the_stub():
    result = plan_archive.archive(SAMPLE)
    stub = "- [x] **M1.3** Old — done; " + plan_archive.STUB_MARK
    assert stub + "\n  old note" in result.task_blocks
    assert stub + "\n- [~]" in result.plan_text


def test_archive_is_idempotent():
    first = plan_archive.archive(SAMPLE, keep_log=2)
    second = plan_archive.archive(first.plan_text, keep_log=2)
    assert second.plan_text == first.plan_text
    assert not second.task_blocks and not second.log_rows and not second.blocker_rows


def test_archive_keeps_only_the_last_work_log_rows_in_order():
    result = plan_archive.archive(SAMPLE, keep_log=2)
    assert result.log_rows == ["| 2026-01-01 | A | one | a |", "| 2026-01-02 | A | two | b |"]
    assert "|---|---|---|---|\n| 2026-01-03 | A | three | c |\n| 2026-01-04 | A | four | d |" in result.plan_text


def test_archive_moves_only_rows_whose_status_starts_answered():
    result = plan_archive.archive(SAMPLE)
    assert result.blocker_rows == ["| Q1 | One | A | **answered** yes |", "| ~~Q2~~ | Two | A | **answered** yes |"]
    for value in ("Q3", "Q4", "Q5"):
        assert f"| {value} |" in result.plan_text


def test_archive_never_touches_open_tasks_or_the_legend():
    result = plan_archive.archive(SAMPLE)
    for block in ("- [~] **M<milestone>.<task>** example", "- [~] **M1.4** Claimed\n  claimed note", "- [?] **M1.5** Review\n  review note", "- [!] **M1.6** Blocked\n  blocked note"):
        assert block in result.plan_text


def test_archive_clips_long_titles_and_handles_missing_priority():
    title = "word " * 40
    text = SAMPLE.replace("## 3. Decisions", f"- [x] **M1.7** {title}— owner\n- [x] **M1.8** — owner\n## 3. Decisions")
    result = plan_archive.archive(text)
    line = next(line for line in result.plan_text.splitlines() if "**M1.7**" in line)
    assert "word… — done;" in line and "`P" not in line
    assert "- [x] **M1.8** — done; " + plan_archive.STUB_MARK in result.plan_text


def test_archive_rejects_text_without_the_task_sections():
    with pytest.raises(ValueError, match="## 2"):
        plan_archive.archive("## 0. Legend\n")


STUB = " — done; " + plan_archive.STUB_MARK
COLLAPSIBLE = f"""## 2. Tasks
### M1 — Literature · status: `[x]`
- [x] **M1.1** First — `P1`{STUB}
- [x] **M1.2** Parent — `P2`{STUB}
  - [ ] **M1.2.1** Open child
- [x] **M1.3** Third — `P1`{STUB}
- [?] **M1.4** In review
- [-] **M1.5** Dropped — `P3` — dropped; {plan_archive.STUB_MARK}
### M2 — Screening
- [x] **M2.1** Other milestone — `P1`{STUB}
## 3. Decisions
"""
ARCHIVED = "- [x] **M1.1** First\\n- [x] **M1.2** Parent\\n- [x] **M1.3** Third\\n- [x] **M2.1** Other\\n".replace("\\n", "\n")


def test_collapse_replaces_closed_stubs_with_one_pointer_that_lists_their_ids():
    result = plan_archive.collapse(COLLAPSIBLE, ARCHIVED, ("M1",))
    assert result.collapsed_ids == ["M1.1", "M1.3"]
    assert "(2). IDs: M1.1, M1.3" in result.plan_text
    assert "**M1.1**" not in result.plan_text.replace("IDs: M1.1", "")
    assert result.missing_from_archive == []


def test_collapse_keeps_open_review_and_dropped_lines_and_a_parent_with_an_open_child():
    text = plan_archive.collapse(COLLAPSIBLE, ARCHIVED, ("M1",)).plan_text
    for kept in ("- [x] **M1.2** Parent", "  - [ ] **M1.2.1** Open child", "- [?] **M1.4** In review", "- [-] **M1.5** Dropped"):
        assert kept in text
    assert "- [x] **M2.1** Other milestone" in text  # a milestone that was not named is untouched


def test_collapse_is_repeatable_and_adds_later_closures_to_the_same_pointer():
    first = plan_archive.collapse(COLLAPSIBLE, ARCHIVED, ("M1",))
    assert plan_archive.collapse(first.plan_text, ARCHIVED, ("M1",)).plan_text == first.plan_text
    later = first.plan_text.replace("- [?] **M1.4** In review", f"- [x] **M1.4** In review{STUB}")
    second = plan_archive.collapse(later, ARCHIVED + "- [x] **M1.4** In review\n", ("M1",))
    assert "(3). IDs: M1.1, M1.3, M1.4" in second.plan_text


def test_collapse_reports_a_stub_whose_id_is_not_in_the_archive_so_no_id_is_lost():
    result = plan_archive.collapse(COLLAPSIBLE, "- [x] **M1.1** First\n", ("M1",))
    assert [line.split("**")[1] for line in result.missing_from_archive] == ["M1.3"]


def test_collapse_names_a_missing_milestone_instead_of_silently_doing_nothing():
    with pytest.raises(ValueError, match="### M9"):
        plan_archive.collapse(COLLAPSIBLE, ARCHIVED, ("M9",))
