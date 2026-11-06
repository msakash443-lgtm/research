"""X.54: M2.8, M2.8.1 and M2.10 may not be `[x]` in plan.md until the owner's G3 approval is recorded in
the Decisions log (plan.md §3). The approval itself happens in the app (the owner approves gate G3); a test
database cannot see it, so this check reads plan.md only. Pure text checks, no DB, no HTTP."""
import re
from pathlib import Path

import pytest

PLAN = Path(__file__).resolve().parents[2] / "plan.md"
GUARDED = ("M2.8", "M2.8.1", "M2.10")

_CLOSED_LINE = re.compile(r"^\s*- \[x\] \*\*(M2\.8\.1|M2\.8|M2\.10)\*\*", re.MULTILINE)


def _section(text: str, start: str, end: str) -> str:
    head = text.index(start)
    tail = text.index(end, head)
    return text[head:tail]


def _has_task_id(row: str, task: str) -> bool:
    # "M2.8" must not match inside "M2.8.1" or "M2.10"
    return re.search(rf"(?<![\d.]){re.escape(task)}(?![\d.])", row) is not None


def closed_without_g3(plan_text: str) -> list[str]:
    """Guarded tasks marked `[x]` with no Decisions-log row that names them and records G3 as approved."""
    decisions = _section(plan_text, "## 3. Decisions log", "## 4.")
    rows = [line for line in decisions.splitlines() if line.startswith("|")]
    flagged = []
    for task in dict.fromkeys(m.group(1) for m in _CLOSED_LINE.finditer(plan_text)):
        approved = any(_has_task_id(r, task) and re.search(r"G3.{0,40}approved", r, re.IGNORECASE) for r in rows)
        if not approved:
            flagged.append(task)
    return flagged


def test_the_current_plan_has_no_guarded_task_closed_without_g3_approval():
    assert closed_without_g3(PLAN.read_text(encoding="utf-8")) == []


def test_a_closed_guarded_task_without_a_decisions_row_is_flagged():
    text = "## 3. Decisions log\n| 2026-10-11 | user | other decision |\n## 4. Blockers\n- [x] **M2.8.1** done\n"
    assert closed_without_g3(text) == ["M2.8.1"]


def test_a_decisions_row_recording_g3_approval_clears_the_flag():
    text = (
        "## 3. Decisions log\n"
        "| 2026-10-14 | owner | G3 approved for M2.8.1 included set | owner |\n"
        "## 4. Blockers\n- [x] **M2.8.1** done\n"
    )
    assert closed_without_g3(text) == []


def test_a_work_log_row_cannot_stand_in_for_the_owner_approval():
    text = (
        "## 3. Decisions log\n"
        "## 4. Blockers\n- [x] **M2.10** done\n"
        "## 5. Work log\n| 2026-10-14 | claude | M2.10 | G3 approved, closing |\n"
    )
    assert closed_without_g3(text) == ["M2.10"]


def test_m2_8_is_not_satisfied_by_an_approval_row_for_m2_8_1():
    text = (
        "## 3. Decisions log\n"
        "| 2026-10-14 | owner | G3 approved for M2.8.1 | owner |\n"
        "## 4. Blockers\n- [x] **M2.8** done\n"
    )
    assert closed_without_g3(text) == ["M2.8"]


@pytest.mark.parametrize("status", ["[ ]", "[?]", "[~]", "[!]"])
def test_an_open_guarded_task_is_not_flagged(status):
    text = f"## 3. Decisions log\n## 4. Blockers\n- {status} **M2.10** open\n"
    assert closed_without_g3(text) == []
