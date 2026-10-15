import ast
import subprocess
import sys
from collections import Counter
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

_MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
_RELEASED_IDS = _MIGRATIONS / "revision_ids.txt"
_REPO_ROOT = _MIGRATIONS.parents[1]


def _declared_revisions(versions: Path) -> list[tuple[str, str]]:
    """(revision, name) for every migration file, read without importing it (Alembic drops duplicates).

    The name is the file stem without its leading revision id, e.g. `provenance_reports`.
    """
    declared = []
    for path in sorted(versions.glob("*.py")):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            target = node.targets[0] if isinstance(node, ast.Assign) else getattr(node, "target", None)
            if isinstance(target, ast.Name) and target.id == "revision":
                revision = node.value.value
                declared.append((revision, path.stem.removeprefix(f"{revision}_")))
    return declared


def _released_revisions(listing: Path) -> list[tuple[str, str]]:
    lines = (line.strip() for line in listing.read_text(encoding="utf-8").splitlines())
    return [tuple(line.split(maxsplit=1)) for line in lines if line and not line.startswith("#")]


def _duplicate_ids(revisions: list[tuple[str, str]]) -> list[str]:
    return sorted(rev for rev, count in Counter(rev for rev, _ in revisions).items() if count > 1)


def _changed_releases(released: list[tuple[str, str]], declared: list[tuple[str, str]]) -> list[str]:
    """Released ids that vanished or now belong to a different migration."""
    names = dict(declared)
    return [f"{rev} ({name} -> {names.get(rev, 'missing')})" for rev, name in released if names.get(rev) != name]


def test_alembic_has_a_single_head():
    """Two heads make `alembic upgrade head` and `alembic check` fail on a clean database."""
    config = Config()
    config.set_main_option("script_location", str(_MIGRATIONS))
    heads = ScriptDirectory.from_config(config).get_heads()
    assert len(heads) == 1, f"Alembic has multiple heads {sorted(heads)}; add a merge revision."


def test_alembic_heads_cli_loads_every_migration():
    """`alembic heads` imports each migration without running env.py, so `app` must come from prepend_sys_path.

    Alembic 1.14 splits prepend_sys_path on spaces, commas and colons, which breaks an absolute
    `%(here)s` path on Windows (drive colon) or under a folder with a space in its name.
    """
    result = subprocess.run(
        [sys.executable, "-I", "-m", "alembic", "-c", "alembic.ini", "heads"],
        cwd=_REPO_ROOT, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.count("(head)") == 1, result.stdout


def test_revision_ids_are_unique():
    """Alembic only warns about a repeated id and silently uses one of the files."""
    duplicates = _duplicate_ids(_declared_revisions(_MIGRATIONS / "versions"))
    assert not duplicates, f"Revision ids declared more than once: {duplicates}; give the newer migration a new id."


def test_released_revision_ids_are_unchanged():
    """Databases store these ids in alembic_version; renumbering one strands them on the wrong revision."""
    declared = _declared_revisions(_MIGRATIONS / "versions")
    released = _released_revisions(_RELEASED_IDS)
    changed = _changed_releases(released, declared)
    unlisted = sorted(set(declared) - set(released))
    assert not _duplicate_ids(released), f"{_RELEASED_IDS.name} lists ids twice: {_duplicate_ids(released)}"
    assert not changed, (
        f"Released revision ids were renumbered or removed: {changed}. Restore the original `revision` "
        "and file name; renumber only the migration that has not reached main yet (plan.md rule 52)."
    )
    assert not unlisted, f"New migrations not listed in {_RELEASED_IDS.name}: {unlisted}; append them."


def test_checks_catch_a_duplicate_and_a_renumbered_id(tmp_path):
    versions = tmp_path / "versions"
    versions.mkdir()
    (versions / "0001_ledger.py").write_text('revision = "0001"\n', encoding="utf-8")
    (versions / "0001_gaps.py").write_text('revision: str = "0001"\n', encoding="utf-8")
    # `provenance` was released as 0002; renumbering it to 0003 handed 0002 to `bibliometric`.
    (versions / "0002_bibliometric.py").write_text('revision = "0002"\n', encoding="utf-8")
    (versions / "0003_provenance.py").write_text('revision = "0003"\n', encoding="utf-8")
    listing = tmp_path / "revision_ids.txt"
    listing.write_text("# header\n0002 provenance\n0004 dropped\n", encoding="utf-8")

    declared = _declared_revisions(versions)

    assert _duplicate_ids(declared) == ["0001"]
    assert _changed_releases(_released_revisions(listing), declared) == [
        "0002 (provenance -> bibliometric)",
        "0004 (dropped -> missing)",
    ]
