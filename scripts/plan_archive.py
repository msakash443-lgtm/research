"""Archive completed plan detail and old log rows.

Usage: python scripts/plan_archive.py [--write] [--file PATH] [--archive-dir DIR]
"""
import argparse
import datetime
import re
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PLAN = Path(__file__).resolve().parents[1] / "plan.md"
DEFAULT_ARCHIVE = DEFAULT_PLAN.parent / "docs" / "plan-archive"
TASK_RE = re.compile(r"^(\s*)- \[(.)\] \*\*([^*]+)\*\*\s*(.*)$")
STUB_MARK = "detail: `docs/plan-archive/tasks-done.md`"
TITLE_MAX = 160
KEEP_LOG_ROWS = 25
ANSWERED_PREFIX = "**answered**"
BLOCKER_NOTE = "> Answered questions (Status cell starting **answered**) are archived verbatim in `docs/plan-archive/blockers-answered.md`."
Q_RE = re.compile(r"^\| (~~)?Q\d+(~~)? \|")


@dataclass(frozen=True)
class ArchiveResult:
    plan_text: str
    task_blocks: list[str]
    log_rows: list[str]
    blocker_rows: list[str]


def archive(plan_text: str, keep_log: int = KEEP_LOG_ROWS) -> ArchiveResult:
    if keep_log < 0:
        raise ValueError("keep_log must not be negative")
    lines = plan_text.split("\n")

    def heading(prefix):
        for index, line in enumerate(lines):
            if line.startswith(prefix):
                return index
        raise ValueError(f"Missing heading: {prefix}")

    s2, s3, s4, s4a, s5 = (heading(prefix) for prefix in
                           ("## 2.", "## 3.", "## 4. ", "## 4a.", "## 5."))
    if not s2 < s3 < s4 < s4a < s5:
        raise ValueError("Plan section headings are out of order")
    log_indices = [i for i in range(s5, len(lines)) if lines[i].startswith("| 20")]
    old_log = set(log_indices[:max(0, len(log_indices) - keep_log)])
    kept_log = log_indices[len(old_log):]
    separators = [i for i in range(s5, len(lines)) if lines[i] == "|---|---|---|---|"]
    blank_log = set()
    if separators and kept_log:
        blank_log = {i for i in range(separators[0] + 1, kept_log[0]) if not lines[i]}

    tasks, logs, blockers, output = [], [], [], []
    insert_note = BLOCKER_NOTE not in lines
    i = 0
    while i < len(lines):
        line = lines[i]
        if i in old_log:
            logs.append(line)
        elif i in blank_log:
            pass
        elif s4 < i < s4a and Q_RE.match(line) and line.rstrip().rstrip("|").rsplit("|", 1)[-1].strip().startswith(ANSWERED_PREFIX):
            blockers.append(line)
        elif (s2 < i < s3 or s4 < i < s4a) and (match := TASK_RE.match(line)):
            indent, mark, task_id, rest = match.groups()
            end = i + 1
            bound = s3 if i < s3 else s4a
            while end < bound:
                following = lines[end]
                if not following.strip() or TASK_RE.match(following) or following.startswith("#"):
                    break
                if len(following) - len(following.lstrip()) <= len(indent):
                    break
                end += 1
            block = lines[i:end]
            if mark in ("x", "-") and STUB_MARK not in line:
                tasks.append("\n".join(block))
                priority = re.search(r"\s+—\s+`(P[0-3])`", rest)
                title = rest[:priority.start()] if priority else ("" if rest.startswith("— ") else rest.split(" — ")[0])
                if len(title) > TITLE_MAX:
                    clipped = title[:TITLE_MAX - 1]
                    title = (clipped.rsplit(" ", 1)[0] if " " in clipped else clipped) + "…"
                prio = f" — `{priority.group(1)}`" if priority else ""
                word = "done" if mark == "x" else "dropped"
                output.append(f"{indent}- [{mark}] **{task_id}**{(' ' + title) if title else ''}{prio} — {word}; {STUB_MARK}")
            elif mark in ("x", "-") and STUB_MARK in line and len(block) > 1:
                tasks.append("\n".join(block))
                output.append(line)
            else:
                output.extend(block)
            i = end
            continue
        else:
            output.append(line)
            if i == s4 and insert_note:
                output.append(BLOCKER_NOTE)
        i += 1
    return ArchiveResult("\n".join(output), tasks, logs, blockers)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--archive-dir", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--keep-log", type=int, default=KEEP_LOG_ROWS)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    with args.file.open(encoding="utf-8", newline="\n") as source:
        text = source.read()
    if not args.archive_dir.is_dir():
        raise FileNotFoundError(args.archive_dir)
    result = archive(text, args.keep_log)
    print(f"Task blocks: {len(result.task_blocks)}; work log rows: {len(result.log_rows)}; blocker rows: {len(result.blocker_rows)}")
    print(f"Plan bytes: {len(text.encode('utf-8'))} -> {len(result.plan_text.encode('utf-8'))}")
    if args.write:
        if result.task_blocks:
            with (args.archive_dir / "tasks-done.md").open("a", encoding="utf-8", newline="\n") as target:
                target.write(f"\n## Moved {datetime.date.today().isoformat()} by scripts/plan_archive.py\n\n")
                target.write("\n".join(result.task_blocks) + "\n")
        if result.log_rows:
            with (args.archive_dir / "work-log-2026-10-01_to_archive.md").open("a", encoding="utf-8", newline="\n") as target:
                target.write("\n".join(result.log_rows) + "\n")
        if result.blocker_rows:
            path = args.archive_dir / "blockers-answered.md"
            if not path.exists():
                with path.open("w", encoding="utf-8", newline="\n") as target:
                    target.write("# Archived answered blocker rows (verbatim, moved from plan.md §4)\n\n| ID | Question / blocker | Raised by | Status |\n|---|---|---|---|\n")
            with path.open("a", encoding="utf-8", newline="\n") as target:
                target.write("\n".join(result.blocker_rows) + "\n")
        with args.file.open("w", encoding="utf-8", newline="\n") as target:
            target.write(result.plan_text)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
