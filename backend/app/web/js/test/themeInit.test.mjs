import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

// X.245: theme-init.js is a classic script run before first paint. Load it the way a browser does (a global
// scope with a document) and check what it leaves on <html>.
const source = readFileSync(fileURLToPath(new URL("../theme-init.js", import.meta.url)), "utf8");

function page({ saved = {}, prefersDark = false, themes = "scholarly modern dense", theme = "scholarly", throwingStorage = false } = {}) {
  const attrs = { "data-theme": theme, "data-themes": themes, "data-mode": "light" };
  const html = { getAttribute: (n) => attrs[n] ?? null, setAttribute: (n, v) => { attrs[n] = v; } };
  const store = { ...saved };
  const localStorage = throwingStorage
    ? { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } }
    : { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = v; } };
  const document = { documentElement: html };
  const matchMedia = () => ({ matches: prefersDark });
  const context = { document, localStorage, matchMedia };
  context.globalThis = context;
  vm.runInNewContext(source, context);
  return { attrs, store, RaTheme: context.RaTheme, controls: context.RaTheme.page };
}

test("a saved registered theme and mode win over the server default and the OS preference", () => {
  const { attrs } = page({ saved: { "ra.theme": "dense", "ra.mode": "dark" }, prefersDark: false });
  assert.equal(attrs["data-theme"], "dense");
  assert.equal(attrs["data-mode"], "dark");
});

test("a saved theme that is no longer registered falls back to the server default", () => {
  const { attrs } = page({ saved: { "ra.theme": "retired" }, theme: "modern" });
  assert.equal(attrs["data-theme"], "modern");
});

test("without a saved mode the OS colour scheme decides", () => {
  assert.equal(page({ prefersDark: true }).attrs["data-mode"], "dark");
  assert.equal(page({ prefersDark: false }).attrs["data-mode"], "light");
  assert.equal(page({ saved: { "ra.mode": "garbage" }, prefersDark: true }).attrs["data-mode"], "dark");
});

test("blocked site data does not stop the page from getting a theme", () => {
  const { attrs, controls } = page({ throwingStorage: true, theme: "modern" });
  assert.equal(attrs["data-theme"], "modern");
  assert.equal(controls.setMode("dark"), true);
  assert.equal(attrs["data-mode"], "dark");
});

test("choosing a theme or mode applies it and remembers it; unknown values are refused", () => {
  const { attrs, store, controls } = page();
  assert.equal(controls.setTheme("modern"), true);
  assert.equal(controls.setMode("dark"), true);
  assert.deepEqual([attrs["data-theme"], attrs["data-mode"], store["ra.theme"], store["ra.mode"]], ["modern", "dark", "modern", "dark"]);
  assert.equal(controls.setTheme("nope"), false);
  assert.equal(controls.setMode("sepia"), false);
  assert.equal(attrs["data-theme"], "modern");
});
