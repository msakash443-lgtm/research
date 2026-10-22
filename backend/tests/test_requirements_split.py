"""X.141: the web app's requirements carry only what `backend/app` needs at run time.

The vendored ARC retrieval service (`backend/arc_retrieval/`) and the test tools have their
own requirement files, so the web image (which installs `backend/requirements.txt` only) ships
neither ARC's scraping/AGPL libraries nor pytest.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

ARC_PACKAGES = {"crawl4ai", "scholarly", "tavily-python", "pymupdf"}
DEV_PACKAGES = {"pytest", "pytest-xdist"}
# Import names of the ARC packages (PyMuPDF imports as `fitz` or `pymupdf`).
ARC_MODULES = {"crawl4ai", "scholarly", "tavily", "fitz", "pymupdf"}


def _pinned(path: Path) -> dict[str, str]:
    """Package name (lower case) -> pinned version, for each `name==version` line."""
    pins = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?==(\S+)", line)
        if match:
            pins[match.group(1).lower()] = match.group(2)
    return pins


def test_the_app_requirements_hold_no_arc_or_test_packages():
    app = _pinned(BACKEND / "requirements.txt")
    assert not (ARC_PACKAGES | DEV_PACKAGES) & set(app)


def test_arc_and_dev_requirements_pin_every_moved_package():
    assert ARC_PACKAGES <= set(_pinned(BACKEND / "requirements-arc.txt"))
    assert DEV_PACKAGES <= set(_pinned(BACKEND / "requirements-dev.txt"))


def test_no_module_under_backend_app_imports_an_arc_package():
    offenders = []
    for path in (BACKEND / "app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            offenders += [f"{path.name}: {name}" for name in names if name.split(".")[0] in ARC_MODULES]
    assert offenders == []
