"""Exit 1 when the Alembic migration chain has more than one head (used by scripts/hooks/pre-push).

Reads the migration files only; no database connection is needed.
"""
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = REPO_ROOT / "backend" / "migrations"


def find_heads(script_location: Path) -> list[str]:
    config = Config()
    config.set_main_option("script_location", str(script_location))
    return sorted(ScriptDirectory.from_config(config).get_heads())


def main() -> int:
    # Migrations import `app` (e.g. guard SQL), as pytest.ini's `pythonpath = backend` provides in tests.
    sys.path.insert(0, str(REPO_ROOT / "backend"))
    heads = find_heads(MIGRATIONS)
    if len(heads) == 1:
        return 0
    print(
        f"Push refused: Alembic has {len(heads)} heads: {', '.join(heads) or 'none'}.\n"
        "Rebase onto main and re-point your down_revision, or run "
        "`alembic -c alembic.ini merge -m \"merge heads\" <headA> <headB>` (plan.md rules 50, 69).",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
