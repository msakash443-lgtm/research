"""Archive completed plan detail and old log rows.

Usage: python scripts/plan_archive.py [--write] [--file PATH] [--archive-dir DIR] [--collapse M0,M1,X]

`--collapse` (X.229, X.230) removes the one-line `[x]` stubs of the named closed milestones from plan.md and
leaves one pointer line under each milestone heading that lists their IDs, so a dependency such as `deps: M2.8`
can still be read as closed. Every collapsed ID must already be in `docs/plan-archive/tasks-done.md`; a stub whose
ID is missing there is appended to it first, so no ID is ever lost. Open, review, blocked and dropped lines, and
a stub that still has an open child under it, stay where they are. Running it again changes nothing.
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


POINTER_MARK = "> Closed `[x]` tasks of this milestone are archived one line each in `docs/plan-archive/tasks-done.md`"
POINTER_RE = re.compile(r"^" + re.escape(POINTER_MARK) + r" \((\d+)\)\. IDs: (.*)$")


@dataclass(frozen=True)
class CollapseResult:
    plan_text: str
    collapsed_ids: list[str]
    missing_from_archive: list[str]  # stub lines whose ID is not in the archive yet: append them there before writing


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def collapse(plan_text: str, archive_text: str, milestones: tuple[str, ...]) -> CollapseResult:
    """See the module docstring. `milestones` are heading codes such as "M0" or "X" (`### M0 — ...`, `### X — ...`)."""
    lines = plan_text.split("\n")
    collapsed_ids: list[str] = []
    missing: list[str] = []
    for code in milestones:
        start = next((i for i, line in enumerate(lines) if re.match(rf"^### {re.escape(code)}( |$)", line)), None)
        if start is None:
            raise ValueError(f"Missing milestone heading: ### {code}")
        end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ") or lines[i].startswith("### ")), len(lines))
        body = list(range(start + 1, end))
        stub = {}
        for i in body:
            match = TASK_RE.match(lines[i])
            if match and match.group(2) == "x" and STUB_MARK in lines[i]:
                stub[i] = match.group(3)
        removable: set[int] = set()
        for i in sorted(stub, reverse=True):  # children first, so a parent is kept while any child stays
            j = i + 1
            keep = False
            while j < end and lines[j].strip() and _indent(lines[j]) > _indent(lines[i]):
                if j not in removable:
                    keep = True
                    break
                j += 1
            if not keep:
                removable.add(i)
        old_pointer = next((i for i in body if POINTER_RE.match(lines[i])), None)
        known = POINTER_RE.match(lines[old_pointer]).group(2).split(", ") if old_pointer is not None else []
        new_ids = [stub[i] for i in sorted(removable)]
        for i in sorted(removable):
            if f"**{stub[i]}**" not in archive_text:
                missing.append(lines[i])
        if not removable:
            continue
        collapsed_ids.extend(new_ids)
        ids = list(dict.fromkeys(known + new_ids))
        pointer = f"{POINTER_MARK} ({len(ids)}). IDs: {', '.join(ids)}"
        rebuilt = []
        for i in range(start + 1, end):
            if i in removable:
                continue
            if i == old_pointer:
                continue
            rebuilt.append(lines[i])
        lines[start + 1:end] = ["", pointer, *rebuilt] if lines[start + 1].strip() else [pointer, *rebuilt]
    return CollapseResult("\n".join(lines), collapsed_ids, missing)


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
    parser.add_argument("--collapse", default="", help="comma-separated closed milestone codes, e.g. M0,M1,M2,M4,M6")
    args = parser.parse_args(argv)
    with args.file.open(encoding="utf-8", newline="\n") as source:
        text = source.read()
    if not args.archive_dir.is_dir():
        raise FileNotFoundError(args.archive_dir)
    result = archive(text, args.keep_log)
    collapsed = None
    if args.collapse:
        archive_file = args.archive_dir / "tasks-done.md"
        archive_text = archive_file.read_text(encoding="utf-8") + "\n" + "\n".join(result.task_blocks)
        collapsed = collapse(result.plan_text, archive_text, tuple(code.strip() for code in args.collapse.split(",") if code.strip()))
        print(f"Collapsed stubs: {len(collapsed.collapsed_ids)}; appended to the archive first: {len(collapsed.missing_from_archive)}")
        result = ArchiveResult(collapsed.plan_text, result.task_blocks, result.log_rows, result.blocker_rows)
    print(f"Task blocks: {len(result.task_blocks)}; work log rows: {len(result.log_rows)}; blocker rows: {len(result.blocker_rows)}")
    print(f"Plan bytes: {len(text.encode('utf-8'))} -> {len(result.plan_text.encode('utf-8'))}")
    if args.write:
        if result.task_blocks:
            with (args.archive_dir / "tasks-done.md").open("a", encoding="utf-8", newline="\n") as target:
                target.write(f"\n## Moved {datetime.date.today().isoformat()} by scripts/plan_archive.py\n\n")
                target.write("\n".join(result.task_blocks) + "\n")
        if collapsed and collapsed.missing_from_archive:
            with (args.archive_dir / "tasks-done.md").open("a", encoding="utf-8", newline="\n") as target:
                target.write(f"\n## Collapsed {datetime.date.today().isoformat()} by scripts/plan_archive.py --collapse (stub lines not archived before)\n\n")
                target.write("\n".join(collapsed.missing_from_archive) + "\n")
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
