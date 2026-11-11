"""Stops test duplication from growing back (X.186): one name per test function across the suite, and the
shared setup helpers live in `api_helpers.py` instead of being copied into each file."""

import ast
from collections import defaultdict
from pathlib import Path

TESTS = Path(__file__).parent

# Setup helpers that were copied into dozens of files (X.179). Use `tests/api_helpers.py`; a file that needs
# something different names its helper for what it does (`_project_with_discipline`, `_team_project`, ...).
BANNED_HELPER_NAMES = frozenset({"_login", "_project", "_source", "_run"})

# Test names that may repeat across files. Keep this list short and justified: a repeated name usually
# means a copied test that belongs in one parametrized test instead.
ALLOWED_REPEATED_TEST_NAMES: frozenset[str] = frozenset()


def _parsed_test_files():
    for path in sorted(TESTS.glob("test_*.py")):
        yield path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_no_test_function_name_is_defined_in_more_than_one_file():
    files_by_name = defaultdict(set)
    for path, tree in _parsed_test_files():
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                files_by_name[node.name].add(path.name)
    repeated = {
        name: sorted(files)
        for name, files in files_by_name.items()
        if len(files) > 1 and name not in ALLOWED_REPEATED_TEST_NAMES
    }
    assert not repeated, f"test names defined in more than one file (parametrize one test instead): {repeated}"


def test_the_shared_setup_helpers_are_not_redefined_in_test_files():
    copies = {}
    for path, tree in _parsed_test_files():
        found = sorted(
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in BANNED_HELPER_NAMES
        )
        if found:
            copies[path.name] = found
    assert not copies, f"use tests/api_helpers.py (or name the helper for what it does) instead of: {copies}"
