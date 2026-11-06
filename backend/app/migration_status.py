"""Compare the database's applied Alembic revision against the code's migration head (X.162).

The app starts and serves even when the database is behind the code: a column a newer migration
adds is simply missing, so the first screen that reads it 500s with no explanation. Reproduced live
2026-10-29 (X.160 walkthrough): Postgres at `20261020_0086`, code at `20261027_0098`,
`column tasks.started_at does not exist`. `/ready` (and a start-up log line) use this module to say
plainly "database is N migration(s) behind" instead of letting the first affected request 500.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


def script_directory() -> ScriptDirectory:
    config = Config(str(ALEMBIC_INI))
    return ScriptDirectory.from_config(config)


def revisions_behind(db_revision: str | None, script: ScriptDirectory) -> int | None:
    """How many migrations separate `db_revision` from the code's head.

    `None` means "nothing to report": already at head, or the database's revision id is not one the
    code's migration chain knows (a divergent/corrupted `alembic_version`row) — this check only ever
    reports a plain "behind" count, never guesses at a more exotic mismatch.
    """
    head = script.get_current_head()
    if head is None or db_revision == head:
        return None
    try:
        if db_revision is None:
            return len(list(script.walk_revisions(base="base", head=head)))
        # `walk_revisions(base, head)` includes `base` itself (it stops *at* it, not *before* it), so
        # the step already applied must be subtracted to get the count still to apply.
        steps = list(script.walk_revisions(base=db_revision, head=head))
        return len(steps) - 1 if len(steps) > 1 else None
    except Exception:
        return None


def database_migration_status(engine: Engine) -> dict[str, Any] | None:
    """`None` when the database is at the code's migration head, not Alembic-managed (e.g.
    `AUTO_CREATE_SCHEMA` dev/test), or the status can't be determined; otherwise
    `{"behind": N, "db_revision": ..., "code_revision": ..., "message": ...}`.

    Never raises: a broken diagnostics check must not take `/ready` down with it.
    """
    try:
        script = script_directory()
        with engine.connect() as connection:
            if not inspect(connection).has_table("alembic_version"):
                return None
            db_revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception:
        return None
    behind = revisions_behind(db_revision, script)
    if behind is None:
        return None
    return {
        "behind": behind,
        "db_revision": db_revision,
        "code_revision": script.get_current_head(),
        "message": f"database is {behind} migration(s) behind the code; run `alembic upgrade head`.",
    }
