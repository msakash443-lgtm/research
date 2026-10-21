import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "plan_status.py"

_spec = importlib.util.spec_from_file_location("plan_status", SCRIPT)
plan_status = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(plan_status)

SAMPLE = """
| Field | Value |
|---|---|
| Last updated | 2026-10-20
| Current focus | focus text

## 2. Milestones

- [~] **M1.1** Claimed task — `P0` — owner: @a — since: 2026-10-20
- [?] **M1.2** Review task — `P1` — owner: @b
- [ ] **M1.3** Open P0 task — `P0` — deps: M1.1
- [ ] **M1.4** Open P2 task — `P2`
- [x] **M1.5** Done task — `P0`
- [ ] **M1.6** Open P1 task — `P1`
- [!] **M1.7** Blocked task — `P1` — blocked: owner decision

## 3. Decisions log

- [ ] **D.1** Not a task, must not be counted

## 5. Work log (append-only, newest last)

| Date | Who | Tasks | Outcome |
|---|---|---|---|
| 2026-10-19 | @a | M1.1 | first |
| 2026-10-20 | @b | M1.2 | second |
"""


def test_counts_only_section_two_task_lines():
    out = plan_status.summarise(SAMPLE)
    assert "[ ] 3, [!] 1, [?] 1, [x] 1, [~] 1" in out


def test_lists_claimed_and_blocked_tasks_in_full_and_review_tasks_by_id():
    out = plan_status.summarise(SAMPLE)
    claimed = out.split("== Claimed / blocked (full lines) ==")[1].split("==")[0]
    review = out.split("== Awaiting review [?] (IDs only) ==")[1].split("==")[0]
    assert "[~] M1.1" in claimed
    assert "[!] M1.7" in claimed
    assert "M1.2" not in claimed
    assert "M1.2" in review
    assert "M1.3" not in review and "M1.3" not in claimed


def test_open_queue_has_only_unclaimed_p0_and_p1_tasks():
    out = plan_status.summarise(SAMPLE)
    queue = out.split("== Open P0/P1 queue ([ ] only) ==")[1].split("==")[0]
    assert "M1.3" in queue
    assert "M1.6" in queue
    assert "M1.4" not in queue  # P2
    assert "M1.5" not in queue  # done
    assert "D.1" not in queue  # decisions log is not section 2


def test_work_log_shows_only_the_requested_last_rows():
    out = plan_status.summarise(SAMPLE, work_log_rows=1)
    tail = out.split("== Last 1 Work Log rows ==")[1]
    assert "second" in tail
    assert "| first |" not in tail
