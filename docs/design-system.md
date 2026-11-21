# Design system

How the UI is themed and how to add a theme. Direction and rationale: the X.242 reference page (`/assets/design-system.html`, static, no login). Scope: `docs/research-automation-spec.md` §10.

## Layers

| File | Holds |
|---|---|
| `backend/app/web/css/tokens.primitive.css` | Theme-independent scales: `--space-*`, `--font-weight-*`, `--font-size-xs`, `--radius-pill`, `--border-width`, `--focus-ring-*`, `--control-min-size`, `--duration-*`, `--ease-standard`. |
| `backend/app/web/css/themes/<name>.css` | One file per theme. Semantic tokens only, for light mode and `[data-mode="dark"]`. |
| `backend/app/web/css/themes/_template.css` | The token contract and the starting point for a new theme. Never selectable. |
| `backend/app/web/styles.css` | All component CSS. Uses tokens only. |
| `backend/app/web/js/theme-init.js` | Blocking script that sets `data-theme` and `data-mode` on `<html>` before first paint. |

`<html>` carries `data-theme` (the server writes `UI_THEME` there), `data-themes` (every registered theme) and `data-mode` (`light` or `dark`). A user's choice is kept in `localStorage` (`ra.theme`, `ra.mode`); with no saved mode the OS `prefers-color-scheme` decides.

## Token contract

`_template.css` is the contract: every token it defines in a block, every theme must define in the matching block. Dark blocks override colours, shadow and `color-scheme` only; fonts, sizes, radii and spacing come from the light block.

| Group | Tokens |
|---|---|
| Fonts | `--font-body`, `--font-heading`, `--font-mono`, `--font-size-base/-sm/-h1/-h2/-h3`, `--line-height`, `--heading-weight`, `--heading-tracking` |
| Shape and space | `--radius-sm/-md/-lg`, `--pad-y`, `--pad-x`, `--gap` |
| Surfaces | `--color-bg`, `--color-surface`, `--color-surface-alt` |
| Text and borders | `--color-text`, `--color-text-muted`, `--color-border`, `--color-border-strong` |
| Accent | `--color-accent`, `--color-accent-hover`, `--color-accent-fg` (text on accent), `--color-accent-soft`, `--color-focus` |
| Status | `--color-{danger,warning,success,info}-fg` and `-bg` |
| Effects | `--shadow-md`, `--color-overlay`, `color-scheme` |

## Naming rules

- Semantic names say the role, not the colour: `--color-danger-fg`, never `--color-red`.
- Colours: `--color-<role>[-<variant>]`. Status colours come as an `-fg`/`-bg` pair that is designed to be used together.
- A new token goes in `_template.css` and in every theme in the same change, or it is a bug.
- Primitive scales live in `tokens.primitive.css`; a theme must not redefine them.

## Rules for component CSS

- No raw colours (`#hex`, `rgb()`, `hsl()`) in `styles.css`. Use `var(--color-*)`.
- No pixel border radii. Use `var(--radius-*)`.
- Font sizes use `--font-size-*`. The one allowed `px` size is the 16px mobile input (prevents iOS zoom).
- Do not force `color-scheme` outside a theme.
- Solid status buttons use the status `-fg` on `-bg` pair, not an accent.

## Add a theme

1. Copy `backend/app/web/css/themes/_template.css` to `css/themes/<name>.css`. The name is lowercase letters and hyphens, and is also the value of `UI_THEME`.
2. Replace `your-theme` in both selectors and fill every token in both blocks.
3. Use six-digit hex for every colour except `--color-overlay` (the contrast test reads them).
4. Link the file in `index.html`, in `SHELL` in `sw.js`, and in `design-system.html`; add the name to `THEMES` in `design-system.js`.
5. Run `pytest backend/tests/test_design_system.py backend/tests/test_ui_themes.py`.

The theme registers itself from its file name (`app/ui_themes.py`). `UI_THEME=<name>` in `.env` makes it the default; an unknown name stops startup with the list of valid ones. Users can still pick any registered theme from the topbar.

## Contrast requirements

`test_design_system.py` checks every theme in both modes:

| Pair | Minimum |
|---|---|
| `text` on `bg`, `surface`, `surface-alt`, `accent-soft` | 4.5:1 |
| `text-muted` on `bg`, `surface`, `surface-alt` | 4.5:1 |
| `accent` on `bg`, `surface`, `accent-soft` | 4.5:1 |
| `accent-fg` on `accent` and `accent-hover` | 4.5:1 |
| each status `-fg` on its `-bg` and on `surface` | 4.5:1 |
| `border-strong` and `focus` on `bg` and `surface` | 3:1 |

## Known gaps

- `.prov-*` provenance tints in `styles.css` use undefined variables (X.248).
