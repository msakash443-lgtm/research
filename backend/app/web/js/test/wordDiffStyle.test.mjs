import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";

// X.125 / X.244: the word diff must stay readable on every theme. The stylesheet only names semantic tokens,
// so the contrast contract is checked on the values each theme gives those tokens (WCAG AA, 4.5:1).
const css = readFileSync(fileURLToPath(new URL("../../styles.css", import.meta.url)), "utf8");
const themeDir = fileURLToPath(new URL("../../css/themes/", import.meta.url));

function tokenOf(selector) {
  const rule = css.match(new RegExp(`${selector}\\s*\\{[^}]*color:\\s*var\\((--[a-z-]+)\\)`));
  assert.ok(rule, `no token colour rule found for ${selector}`);
  return rule[1];
}

function luminance(hex) {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const lin = (c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

function contrast(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

// Every `[data-theme="x"]` / `[data-theme="x"][data-mode="dark"]` block of every real theme file (the template is skipped).
function themeBlocks() {
  const blocks = [];
  for (const file of readdirSync(themeDir).filter((f) => f.endsWith(".css") && !f.startsWith("_"))) {
    const text = readFileSync(themeDir + file, "utf8");
    for (const m of text.matchAll(/(\[data-theme="[^"]+"\](?:\[data-mode="dark"\])?)\s*\{([^}]*)\}/g)) {
      blocks.push({ name: `${file} ${m[1]}`, tokens: Object.fromEntries([...m[2].matchAll(/(--[a-z-]+):\s*(#[0-9a-fA-F]{6})/g)].map((t) => [t[1], t[2]])) });
    }
  }
  return blocks;
}

test("the contrast helper rejects a known low-contrast pair", () => {
  // #777777 on white is about 4.48:1, just under AA, so it must not pass.
  assert.ok(contrast("#777777", "#ffffff") < 4.5);
  assert.ok(contrast("#000000", "#ffffff") > 4.5);
});

test("deleted and inserted words meet WCAG AA contrast on the card and editor surfaces of every theme and mode", () => {
  const del = tokenOf(String.raw`\.write-word-diff del`);
  const ins = tokenOf(String.raw`\.write-word-diff ins`);
  const blocks = themeBlocks();
  assert.ok(blocks.length >= 6, "expected the three themes in light and dark");
  for (const { name, tokens } of blocks) {
    for (const token of [del, ins]) {
      for (const surface of ["--color-surface", "--color-bg"]) {
        assert.ok(tokens[token] && tokens[surface], `${name} is missing ${token} or ${surface}`);
        assert.ok(contrast(tokens[token], tokens[surface]) >= 4.5, `${name}: ${token} on ${surface} is below AA`);
      }
    }
  }
});

test("deleted and inserted words use different colours, so the change does not depend on line style alone", () => {
  assert.notEqual(tokenOf(String.raw`\.write-word-diff del`), tokenOf(String.raw`\.write-word-diff ins`));
});

test("components leave the colour scheme to the theme, so dark mode can switch every surface at once", () => {
  assert.doesNotMatch(css, /color-scheme\s*:/);
});

test("the hidden removed/added labels have a visually-hidden rule that keeps them out of the layout", () => {
  assert.match(css, /\.sr-only\s*\{[^}]*position:absolute/);
});
