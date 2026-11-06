"""Compare per-table row counts between a SQLite snapshot and a Postgres database.

Read-only on both sides. The Postgres URL is taken from the PG_URL environment
variable (never from .env, never printed), e.g. in PowerShell:

    $env:PG_URL = "postgresql+psycopg://research_ai:<password>@localhost:5432/research_ai"
    python scripts/compare_row_counts.py research_ai.db.bak-20261013-120002

Exit code 0 when every compared table matches, 1 on any difference, 2 on usage error.
Tables the 2026-10-13 migration deliberately did not load (alembic_version,
zotero_syncs) are reported but not counted as failures.
"""
import os
import sqlite3
import sys

from sqlalchemy import create_engine, inspect, text

SKIPPED = {"alembic_version", "zotero_syncs"}


def sqlite_counts(path: str) -> dict[str, int]:
    uri = "file:" + os.path.abspath(path).replace("\\", "/") + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        names = [
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        return {n: con.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0] for n in names}
    finally:
        con.close()


def pg_counts(url: str) -> dict[str, int]:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            if conn.dialect.name == "postgresql":
                conn.execute(text("SET TRANSACTION READ ONLY"))
            names = inspect(conn).get_table_names()
            return {
                n: conn.execute(text(f'SELECT COUNT(*) FROM "{n}"')).scalar_one()
                for n in names
            }
    finally:
        engine.dispose()


def main(argv: list[str]) -> int:
    url = os.environ.get("PG_URL", "")
    if len(argv) != 2 or not url:
        print("usage: set PG_URL, then: python scripts/compare_row_counts.py <sqlite-file>")
        return 2
    lite = sqlite_counts(argv[1])
    pg = pg_counts(url)
    bad = 0
    print(f"{'table':40} {'sqlite':>8} {'postgres':>9}  result")
    for name in sorted(set(lite) | set(pg)):
        a, b = lite.get(name), pg.get(name)
        if a == b:
            verdict = "ok"
        elif name in SKIPPED:
            verdict = "not loaded (expected)"
        else:
            verdict = "MISSING in sqlite" if a is None else "MISSING in postgres" if b is None else "MISMATCH"
            bad += 1
        print(f"{name:40} {a if a is not None else '-':>8} {b if b is not None else '-':>9}  {verdict}")
    print(f"\n{bad} table(s) differ" if bad else "\nall compared tables match")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
