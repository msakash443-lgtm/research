// Regression for X.258: steps/evidence.js called comparisonPanel(), which nothing defined or imported, so the Evidence
// step threw on every render. appImports.test.mjs only knows names that js/ modules export, so it cannot see a function
// that exists nowhere. This flags a bare call `name(` whose name never appears anywhere else in the same file (no
// declaration, import, parameter or other use) and is not a JS/browser global.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const stepsDir = join(dirname(fileURLToPath(import.meta.url)), "..", "steps");
const KEYWORDS = new Set(["if", "for", "while", "switch", "catch", "function", "return", "typeof", "await", "async", "new", "import", "super", "void", "delete", "else", "do", "in", "of", "case", "throw", "yield"]);
const BROWSER_GLOBALS = new Set(["alert", "confirm", "prompt", "requestAnimationFrame", "cancelAnimationFrame", "getComputedStyle", "matchMedia", "fetch", "structuredClone", "queueMicrotask"]);

function stripNonCode(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:"'`\\])\/\/.*$/gm, "$1")
    .replace(/`(?:[^`\\]|\\.)*`/g, "``")
    .replace(/"(?:[^"\\\n]|\\.)*"/g, '""')
    .replace(/'(?:[^'\\\n]|\\.)*'/g, "''");
}

export function undefinedCalls(src) {
  const code = stripNonCode(src);
  const missing = new Set();
  // A name followed by `(args) {` is a method definition (dispatchTransaction(tr) {), not a call.
  for (const m of code.matchAll(/(?<![\w$.])(?<!function\s+)([A-Za-z_$][\w$]*)\s*\((?![^()]*\)\s*\{)/g)) {
    const name = m[1];
    if (KEYWORDS.has(name) || BROWSER_GLOBALS.has(name) || name in globalThis) continue;
    const id = name.replace(/[$]/g, "\\$");
    const uses = [...code.matchAll(new RegExp(`(?<![\\w$])${id}(?![\\w$])`, "g"))];
    const calls = [...code.matchAll(new RegExp(`(?<![\\w$.])(?<!function\\s+)${id}\\s*\\((?![^()]*\\)\\s*\\{)`, "g"))];
    if (uses.length === calls.length) missing.add(name);
  }
  return [...missing].sort();
}

test("no steps/*.js file calls a function it never defines, imports or receives", () => {
  const problems = {};
  for (const file of readdirSync(stepsDir).filter((name) => name.endsWith(".js"))) {
    const missing = undefinedCalls(readFileSync(join(stepsDir, file), "utf8"));
    if (missing.length) problems[file] = missing;
  }
  assert.deepEqual(problems, {});
});

test("the check catches the X.258 mistake: a call to a function that exists nowhere", () => {
  const src = 'import { el } from "../dom.js";\nasync function render(root) { root.append(await comparisonPanel(root)); el("p"); }\n';
  assert.deepEqual(undefinedCalls(src), ["comparisonPanel"]);
  assert.deepEqual(undefinedCalls(src + "function comparisonPanel() {}\n"), []);
});
