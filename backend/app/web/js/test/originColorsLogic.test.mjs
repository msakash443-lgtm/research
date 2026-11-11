import test from "node:test";
import assert from "node:assert/strict";
import { originStorage, readOriginColors, writeOriginColors } from "../originColorsLogic.js";

function storage() {
  const values = new Map();
  return { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) };
}

test("origin colours default on and persist per project", () => {
  const store = storage();
  assert.equal(readOriginColors(store, "p1"), true);
  writeOriginColors(store, "p1", false);
  assert.equal(readOriginColors(store, "p1"), false);
  writeOriginColors(store, "p1", true);
  assert.equal(readOriginColors(store, "p1"), true);
  assert.equal(readOriginColors(store, "p2"), true);
});

test("blocked storage leaves origin colours enabled and does not block writes", () => {
  const blocked = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };
  assert.equal(readOriginColors(blocked, "p1"), true);
  assert.doesNotThrow(() => writeOriginColors(blocked, "p1", false));
});
test("blocked localStorage getter falls back without throwing", () => {
  const globalObject = {};
  Object.defineProperty(globalObject, "localStorage", {
    get() { throw new Error("blocked"); },
  });
  assert.equal(originStorage(globalObject), null);
});

test("the toggle write path (originStorage + writeOriginColors) survives a throwing localStorage getter", () => {
  const globalObject = {};
  Object.defineProperty(globalObject, "localStorage", { get() { throw new Error("blocked"); } });
  assert.doesNotThrow(() => writeOriginColors(originStorage(globalObject), "p1", false));
});
