"""Design-system guards (X.247): every theme is complete, readable (WCAG AA) and the component CSS stays on tokens.

Pure file checks, no app or network. The token contract is `css/themes/_template.css`: whatever it defines in a
block, every real theme must define in the matching block."""

import re
from pathlib import Path

import pytest

from app.ui_themes import THEME_DIR, registered_themes

WEB = Path(__file__).parent.parent / "app" / "web"
STYLES = WEB / "styles.css"
PRIMITIVES = WEB / "css" / "tokens.primitive.css"
BLOCK = re.compile(r'\[data-theme="([^"]+)"\](\[data-mode="dark"\])?\s*\{([^}]*)\}')
DECLARATION = re.compile(r"(--[a-z0-9-]+)\s*:\s*([^;]+);")
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _blocks(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    found = {}
    for name, dark, body in BLOCK.findall(path.read_text(encoding="utf-8")):
        found[(name, "dark" if dark else "light")] = {k: v.strip() for k, v in DECLARATION.findall(body)}
    return found


TEMPLATE = _blocks(THEME_DIR / "_template.css")
THEMES = {name: _blocks(THEME_DIR / f"{name}.css") for name in registered_themes()}
MODES = ("light", "dark")
CELLS = [(name, mode) for name in registered_themes() for mode in MODES]


def _luminance(hex_colour: str) -> float:
    channels = [int(hex_colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _resolved(name: str, mode: str) -> dict[str, str]:
    """Tokens in effect: the light block, overridden by the dark block in dark mode."""
    tokens = dict(THEMES[name][(name, "light")])
    if mode == "dark":
        tokens.update(THEMES[name][(name, "dark")])
    return tokens


def test_the_contrast_helper_matches_the_published_wcag_extremes():
    assert _contrast("#000000", "#ffffff") == pytest.approx(21.0)
    assert _contrast("#777777", "#888888") < 1.5


def test_the_template_defines_both_modes_so_it_can_serve_as_the_token_contract():
    assert set(TEMPLATE) == {("your-theme", "light"), ("your-theme", "dark")}
    assert len(TEMPLATE[("your-theme", "light")]) > 30


@pytest.mark.parametrize("name,mode", CELLS)
def test_every_theme_defines_every_token_the_template_defines(name, mode):
    required = set(TEMPLATE[("your-theme", mode)])
    missing = required - set(THEMES[name].get((name, mode), {}))
    assert not missing, f"theme {name} ({mode}) is missing: {sorted(missing)}"


@pytest.mark.parametrize("name,mode", CELLS)
def test_every_theme_colour_is_a_six_digit_hex_so_contrast_can_be_checked(name, mode):
    colours = {k: v for k, v in _resolved(name, mode).items() if k.startswith("--color-") and "overlay" not in k}
    bad = {k: v for k, v in colours.items() if not HEX.match(v)}
    assert not bad, f"theme {name} ({mode}) has non-hex colours: {bad}"


# (foreground, background, minimum ratio): body and status text need 4.5:1, borders and the focus ring 3:1 (WCAG 1.4.3, 1.4.11).
TEXT_PAIRS = [
    ("--color-text", bg) for bg in ("--color-bg", "--color-surface", "--color-surface-alt", "--color-accent-soft")
] + [
    ("--color-text-muted", "--color-bg"),
    ("--color-text-muted", "--color-surface"),
    ("--color-text-muted", "--color-surface-alt"),
    ("--color-accent", "--color-bg"),
    ("--color-accent", "--color-surface"),
    ("--color-accent", "--color-accent-soft"),
    ("--color-accent-fg", "--color-accent"),
    ("--color-accent-fg", "--color-accent-hover"),
] + [
    (f"--color-{status}-fg", f"--color-{status}-bg") for status in ("danger", "warning", "success", "info")
] + [
    (f"--color-{status}-fg", "--color-surface") for status in ("danger", "warning", "success", "info")
]
UI_PAIRS = [
    ("--color-border-strong", "--color-surface"),
    ("--color-border-strong", "--color-bg"),
    ("--color-focus", "--color-bg"),
    ("--color-focus", "--color-surface"),
]


@pytest.mark.parametrize("name,mode", CELLS)
def test_every_text_pair_in_every_theme_and_mode_meets_wcag_aa_4_5_to_1(name, mode):
    tokens = _resolved(name, mode)
    failures = {f"{fg} on {bg}": round(_contrast(tokens[fg], tokens[bg]), 2) for fg, bg in TEXT_PAIRS if _contrast(tokens[fg], tokens[bg]) < 4.5}
    assert not failures, f"theme {name} ({mode}) text below 4.5:1: {failures}"


@pytest.mark.parametrize("name,mode", CELLS)
def test_every_border_and_focus_ring_in_every_theme_and_mode_meets_3_to_1(name, mode):
    tokens = _resolved(name, mode)
    failures = {f"{fg} on {bg}": round(_contrast(tokens[fg], tokens[bg]), 2) for fg, bg in UI_PAIRS if _contrast(tokens[fg], tokens[bg]) < 3.0}
    assert not failures, f"theme {name} ({mode}) UI colour below 3:1: {failures}"


def test_component_css_uses_no_raw_colours_and_no_pixel_radii():
    css = STYLES.read_text(encoding="utf-8")
    colours = re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", css)
    radii = re.findall(r"border(?:-[a-z]+){0,2}-radius\s*:\s*[^;}]*\d+px", css)
    assert not colours, f"raw colours in styles.css: {colours[:5]}"
    assert not radii, f"pixel radii in styles.css: {radii[:5]}"


def test_every_semantic_token_the_component_css_uses_is_defined_by_every_theme_or_the_primitives():
    used = set(re.findall(r"var\((--(?:color|font|radius|pad|gap|shadow|line|heading)[a-z0-9-]*)", STYLES.read_text(encoding="utf-8")))
    primitives = {name for name, _ in DECLARATION.findall(PRIMITIVES.read_text(encoding="utf-8"))}
    for name in registered_themes():
        defined = set(THEMES[name][(name, "light")]) | primitives
        assert not used - defined, f"theme {name} lacks tokens used by styles.css: {sorted(used - defined)}"


def test_the_reference_page_offers_every_registered_theme_and_both_modes():
    html = (WEB / "design-system.html").read_text(encoding="utf-8")
    js = (WEB / "design-system.js").read_text(encoding="utf-8")
    listed_in_html = re.findall(r'<option value="([a-z-]+)">', html)
    listed_in_js = re.search(r"const THEMES = \[([^\]]*)\]", js).group(1)
    for name in registered_themes():
        assert f'<link rel="stylesheet" href="/assets/css/themes/{name}.css" />' in html
        assert name in listed_in_html
        assert f'"{name}"' in listed_in_js
    assert {"light", "dark"} <= set(listed_in_html)
    assert set(listed_in_html) <= set(registered_themes()) | {"light", "dark"}


def test_the_app_page_and_the_offline_shell_link_every_registered_theme_file():
    index = (WEB / "index.html").read_text(encoding="utf-8")
    shell = (WEB / "sw.js").read_text(encoding="utf-8")
    for name in registered_themes():
        assert f'<link rel="stylesheet" href="/assets/css/themes/{name}.css" />' in index
        assert f'"/assets/css/themes/{name}.css"' in shell
