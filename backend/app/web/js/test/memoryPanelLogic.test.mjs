// X.95: with browser storage blocked, app.js must still load. The collapsed-panel
// preference is read at module top level, so a throwing localStorage used to stop the app.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { readMemoryCollapsed, writeMemoryCollapsed } from "../memoryPanelLogic.js";

function blockedGlobal() {
  const globalObject = {};
  Object.defineProperty(globalObject, "localStorage", { get() { throw new Error("blocked"); } });
  return globalObject;
}

test("a blocked localStorage getter reads as not collapsed and does not throw", () => {
  assert.equal(readMemoryCollapsed(blockedGlobal()), false);
  assert.doesNotThrow(() => writeMemoryCollapsed(true, blockedGlobal()));
});

test("a storage whose methods throw reads as not collapsed and does not throw on write", () => {
  const throwing = { localStorage: { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } } };
  assert.equal(readMemoryCollapsed(throwing), false);
  assert.doesNotThrow(() => writeMemoryCollapsed(false, throwing));
});

test("the collapsed preference round-trips through a working storage", () => {
  const values = new Map();
  const g = { localStorage: { getItem: (k) => values.get(k) ?? null, setItem: (k, v) => values.set(k, v) } };
  assert.equal(readMemoryCollapsed(g), false);
  writeMemoryCollapsed(true, g);
  assert.equal(readMemoryCollapsed(g), true);
  writeMemoryCollapsed(false, g);
  assert.equal(readMemoryCollapsed(g), false);
});

test("app.js does not touch localStorage directly", () => {
  const appJs = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "app.js");
  assert.doesNotMatch(readFileSync(appJs, "utf8"), /\blocalStorage\b/);
});
