// Run with: node --test backend/app/web/js/test   (no packages; pytest does not run these)
// Regression for X.73: app.js called parseInboxRoute() without importing it, which
// crashed every authenticated page load. `node --check` cannot see that, and no other
// test loads app.js, so this scans it for calls to names the js/ modules export.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const webDir = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const jsDir = join(webDir, "js");

function stripComments(src) {
  return src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`])\/\/.*$/gm, "$1");
}

// JS identifiers may contain $ (dom.js exports `$` and `$$`), which \w does not match
// and which is special in a RegExp, so names are matched with [\w$] and escaped.
function escapeRegExp(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function exportedFunctions(src) {
  const names = new Set();
  for (const m of src.matchAll(/^export\s+(?:async\s+)?function\s+([\w$]+)/gm)) names.add(m[1]);
  for (const m of src.matchAll(/^export\s+const\s+([\w$]+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[\w$]+)\s*=>/gm)) names.add(m[1]);
  return names;
}

function importedNames(src) {
  const names = new Set();
  for (const m of src.matchAll(/^import\s+([^;]+?)\s+from\s+["'][^"']+["']/gm)) {
    const clause = m[1];
    const braces = clause.match(/\{([^}]*)\}/);
    if (braces) {
      for (const part of braces[1].split(",")) {
        const name = part.trim().split(/\s+as\s+/).pop();
        if (name) names.add(name);
      }
    }
    const def = clause.replace(/\{[^}]*\}/, "").replace(/,/g, " ").trim();
    for (const word of def.split(/\s+/)) if (word) names.add(word.replace(/^\*\s*as\s*/, ""));
  }
  return names;
}

function locallyDefined(src, name) {
  const id = escapeRegExp(name);
  return new RegExp(`(?:function|const|let|var)\\s+${id}(?![\\w$])`).test(src);
}

function unresolvedCalls(appSrc, exported) {
  const code = stripComments(appSrc);
  const imported = importedNames(code);
  const missing = [];
  for (const name of exported) {
    const called = new RegExp(`(?<![\\w.$])${escapeRegExp(name)}\\s*\\(`).test(code);
    if (called && !imported.has(name) && !locallyDefined(code, name)) missing.push(name);
  }
  return missing.sort();
}

function allExportedNames() {
  const names = new Set();
  const walk = (dir) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      if (entry.isDirectory()) {
        if (entry.name !== "test") walk(join(dir, entry.name));
      } else if (entry.name.endsWith(".js")) {
        for (const n of exportedFunctions(readFileSync(join(dir, entry.name), "utf8"))) names.add(n);
      }
    }
  };
  walk(jsDir);
  return names;
}

test("app.js imports every js/ module function it calls", () => {
  const appSrc = readFileSync(join(webDir, "app.js"), "utf8");
  assert.deepEqual(unresolvedCalls(appSrc, allExportedNames()), []);
});

test("the check catches the X.73 mistake: a call with its import removed", () => {
  const appSrc = readFileSync(join(webDir, "app.js"), "utf8");
  const broken = appSrc.replace(/^import \{ parseInboxRoute \} from "\.\/js\/inboxLogic\.js";\n/m, "");
  assert.notEqual(broken, appSrc, "the parseInboxRoute import line was not found to remove");
  assert.deepEqual(unresolvedCalls(broken, allExportedNames()), ["parseInboxRoute"]);
});

test("the check covers names with $ in them: the dom.js $ helper with its import removed", () => {
  const appSrc = readFileSync(join(webDir, "app.js"), "utf8");
  const broken = appSrc.replace(/^import \{ \$, \$\$, /m, "import { $$$$, ");
  assert.notEqual(broken, appSrc, "the $ import from dom.js was not found to remove");
  assert.deepEqual(unresolvedCalls(broken, allExportedNames()), ["$"]);
});
