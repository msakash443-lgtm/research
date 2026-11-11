"""Read-only status summary of plan.md.

Prints the header fields, status counts, the claimed and blocked tasks (`[~]`,
`[!]`) in full, the IDs of tasks awaiting review (`[?]`), the open P0/P1 queue
(`[ ]`), and the last Work Log rows. It never writes plan.md; edits stay manual
(plan.md section 1.8).

Usage (from the repo root):
    python scripts/plan_status.py [--work-log N] [--width N] [--file PATH]
"""

import argparse
import re
import sys
from pathlib import Path

DEFAULT_PLAN = Path(__file__).resolve().parents[1] / "plan.md"

TASK_RE = re.compile(r"^\s*- \[(.)\] \*\*([^*]+)\*\*\s*(.*)$")
PRIORITY_RE = re.compile(r"`(P[0-3])`")
HEADER_RE = re.compile(r"^\| (Last updated|Updated by|Current focus|Test baseline) \|\s*(.*?)\s*\|?\s*$")
WORK_LOG_HEADING = "## 5. Work log"
MILESTONE_HEADING = "## 2."
DECISIONS_HEADING = "## 3."


def _section_lines(lines, start_prefix, end_prefix=None):
    """Return the lines from the heading starting with start_prefix up to end_prefix."""
    inside = False
    out = []
    for line in lines:
        if line.startswith(start_prefix):
            inside = True
            continue
        if inside and end_prefix and line.startswith(end_prefix):
            break
        if inside:
            out.append(line)
    return out


def _clip(text, width):
    text = " ".join(text.split())
    return text if len(text) <= width else text[: max(width - 3, 0)] + "..."


def parse_header(lines):
    fields = {}
    for line in lines[:40]:
        match = HEADER_RE.match(line.rstrip("\n"))
        if match:
            fields[match.group(1)] = match.group(2).strip()
    return fields


def parse_tasks(lines):
    """Yield (mark, task_id, priority, rest_of_line) for every task line in section 2."""
    for line in _section_lines(lines, MILESTONE_HEADING, DECISIONS_HEADING):
        match = TASK_RE.match(line.rstrip("\n"))
        if not match:
            continue
        mark, task_id, rest = match.group(1), match.group(2).strip(), match.group(3)
        priority = PRIORITY_RE.search(rest)
        yield mark, task_id, priority.group(1) if priority else "-", rest


def parse_work_log(lines, rows):
    body = [line.rstrip("\n") for line in _section_lines(lines, WORK_LOG_HEADING)]
    data_rows = [line for line in body if line.startswith("| 20")]
    return data_rows[-rows:] if rows > 0 else []


def summarise(text, work_log_rows=5, width=110):
    lines = text.splitlines(keepends=True)
    out = []

    header = parse_header(lines)
    out.append("== Header ==")
    for key in ("Last updated", "Updated by", "Current focus", "Test baseline"):
        if key in header:
            out.append(f"{key}: {_clip(header[key], width * 2)}")

    tasks = list(parse_tasks(lines))

    counts = {}
    for mark, _, _, _ in tasks:
        counts[mark] = counts.get(mark, 0) + 1
    out.append("")
    out.append("== Task counts (section 2) ==")
    out.append(", ".join(f"[{mark}] {count}" for mark, count in sorted(counts.items())))

    out.append("")
    out.append("== Claimed / blocked (full lines) ==")
    claimed = [t for t in tasks if t[0] in ("~", "!")]
    if not claimed:
        out.append("(none)")
    for mark, task_id, priority, rest in claimed:
        out.append(f"[{mark}] {task_id} {priority} {_clip(rest, width)}")

    out.append("")
    out.append("== Awaiting review [?] (IDs only) ==")
    review_ids = [task_id for mark, task_id, _, _ in tasks if mark == "?"]
    out.append(", ".join(review_ids) if review_ids else "(none)")

    out.append("")
    out.append("== Open P0/P1 queue ([ ] only) ==")
    queue = [t for t in tasks if t[0] == " " and t[2] in ("P0", "P1")]
    if not queue:
        out.append("(none)")
    for mark, task_id, priority, rest in queue:
        out.append(f"[{mark}] {task_id} {priority} {_clip(rest, width)}")

    out.append("")
    out.append(f"== Last {work_log_rows} Work Log rows ==")
    rows = parse_work_log(lines, work_log_rows)
    if not rows:
        out.append("(none)")
    for row in rows:
        out.append(_clip(row, width * 2))

    return "\n".join(out) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", type=Path, default=DEFAULT_PLAN, help="plan file to read")
    parser.add_argument("--work-log", type=int, default=5, help="Work Log rows to show")
    parser.add_argument("--width", type=int, default=110, help="max characters per task line")
    args = parser.parse_args(argv)

    if not args.file.is_file():
        print(f"plan file not found: {args.file}", file=sys.stderr)
        return 1
    text = args.file.read_text(encoding="utf-8")
    # Windows consoles default to cp1252; plan.md contains Greek letters and dashes.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stdout.write(summarise(text, work_log_rows=args.work_log, width=args.width))
    return 0


if __name__ == "__main__":
    sys.exit(main())
