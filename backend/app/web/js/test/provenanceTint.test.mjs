import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";

// X.248: editor provenance tints must name defined tokens and keep body text readable (WCAG AA 4.5:1) on every theme and mode.
const web = fileURLToPath(new URL("../../", import.meta.url));
const css = readFileSync(`${web}styles.css`, "utf8");
const primitive = readFileSync(`${web}css/tokens.primitive.css`, "utf8");
const themeDir = `${web}css/themes/`;

const lum = (hex) => {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const lin = (c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
};
const contrast = (a, b) => { const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x); return (hi + 0.05) / (lo + 0.05); };

const rules = [...css.matchAll(/\.(prov-[a-z_]+)\b[^{]*\{\s*background:\s*var\((--[a-z-]+)\)/g)];

test("every provenance class paints a token that tokens.primitive.css defines", () => {
  assert.ok(rules.length >= 4, "expected the four provenance background rules");
  for (const [, cls, token] of rules) assert.match(primitive, new RegExp(`${token}\\s*:`), `${cls} uses undefined ${token}`);
});

test("body text stays readable on every provenance tint in every theme and mode", () => {
  const alias = Object.fromEntries([...primitive.matchAll(/(--color-prov-[a-z-]+):\s*var\((--[a-z-]+)\)/g)].map((m) => [m[1], m[2]]));
  let checked = 0;
  for (const file of readdirSync(themeDir).filter((f) => !f.startsWith("_"))) {
    const src = readFileSync(themeDir + file, "utf8");
    for (const [, head, body] of src.matchAll(/(\[data-theme[^{]*)\{([^}]*)\}/g)) {
      const dark = head.includes('data-mode="dark"');
      const vals = Object.fromEntries([...body.matchAll(/(--[a-z-]+):\s*(#[0-9a-fA-F]{6})/g)].map((m) => [m[1], m[2]]));
      if (!vals["--color-text"]) continue;
      for (const [, , token] of rules) {
        const bg = vals[alias[token]];
        assert.ok(bg, `${file} ${dark ? "dark" : "light"} lacks ${alias[token]}`);
        assert.ok(contrast(vals["--color-text"], bg) >= 4.5, `${file} ${dark ? "dark" : "light"} ${token} contrast too low`);
        checked++;
      }
    }
  }
  assert.ok(checked >= 24, `only ${checked} combinations checked`);
});
