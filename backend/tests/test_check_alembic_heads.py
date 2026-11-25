import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_alembic_heads.py"
_spec = importlib.util.spec_from_file_location("check_alembic_heads", SCRIPT)
check_alembic_heads = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_alembic_heads)


def _migration(versions: Path, revision: str, down_revision: str | None) -> None:
    (versions / f"{revision}.py").write_text(
        f"revision = {revision!r}\ndown_revision = {down_revision!r}\n"
        "def upgrade():\n    pass\n\ndef downgrade():\n    pass\n",
        encoding="utf-8",
    )


def test_two_migrations_on_the_same_parent_are_reported_as_two_heads(tmp_path):
    versions = tmp_path / "versions"
    versions.mkdir()
    _migration(versions, "a1", None)
    _migration(versions, "b2", "a1")
    _migration(versions, "c3", "a1")

    assert check_alembic_heads.find_heads(tmp_path) == ["b2", "c3"]


def test_a_linear_chain_has_one_head_and_the_real_repository_passes(tmp_path, capsys):
    versions = tmp_path / "versions"
    versions.mkdir()
    _migration(versions, "a1", None)
    _migration(versions, "b2", "a1")

    assert check_alembic_heads.find_heads(tmp_path) == ["b2"]
    assert check_alembic_heads.main() == 0
    assert capsys.readouterr().err == ""
