"""Registered UI themes (X.245): one file per theme in `web/css/themes/`, files starting with `_` are templates."""

from functools import lru_cache
from pathlib import Path

THEME_DIR = Path(__file__).parent / "web" / "css" / "themes"
DEFAULT_THEME = "scholarly"


@lru_cache(maxsize=1)
def registered_themes() -> tuple[str, ...]:
    return tuple(sorted(path.stem for path in THEME_DIR.glob("*.css") if not path.name.startswith("_")))
