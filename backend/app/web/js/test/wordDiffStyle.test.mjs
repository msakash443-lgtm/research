import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

// X.125: the word diff must stay readable on its own colours. These tests read the real stylesheet,
// so a colour change that drops below WCAG AA (4.5:1 on the white card and editor backgrounds) fails here.
const css = readFileSync(fileURLToPath(new URL("../../styles.css", import.meta.url)), "utf8");

function colourOf(selector) {
  const rule = css.match(new RegExp(`${selector}\\s*\\{[^}]*color:\\s*(#[0-9a-fA-F]{6})`));
  assert.ok(rule, `no colour rule found for ${selector}`);
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

test("the contrast helper rejects a known low-contrast pair", () => {
  // #777777 on white is about 4.48:1, just under AA, so it must not pass.
  assert.ok(contrast("#777777", "#ffffff") < 4.5);
  assert.ok(contrast("#000000", "#ffffff") > 4.5);
});

test("deleted and inserted words meet WCAG AA contrast on white", () => {
  assert.ok(contrast(colourOf(String.raw`\.write-word-diff del`), "#ffffff") >= 4.5);
  assert.ok(contrast(colourOf(String.raw`\.write-word-diff ins`), "#ffffff") >= 4.5);
});

test("deleted and inserted words use different colours, so the change does not depend on line style alone", () => {
  assert.notEqual(colourOf(String.raw`\.write-word-diff del`), colourOf(String.raw`\.write-word-diff ins`));
});

test("the hidden removed/added labels have a visually-hidden rule that keeps them out of the layout", () => {
  assert.match(css, /\.sr-only\s*\{[^}]*position:absolute/);
});
