import test from "node:test";
import assert from "node:assert/strict";

import { safeHref } from "../dom.js";

test("safeHref refuses script-running schemes however they are disguised", () => {
  for (const hostile of ["javascript:x", "JaVa\tScript:x", " \u0000javascript:x", "data:text/html,x", "vbscript:x"]) {
    assert.equal(safeHref(hostile), null, hostile);
  }
});

test("safeHref keeps web, relative, fragment and blob links", () => {
  for (const fine of ["https://a", "/api/x", "#/p/1", "blob:http://h/1"]) {
    assert.equal(safeHref(fine), fine);
  }
});
