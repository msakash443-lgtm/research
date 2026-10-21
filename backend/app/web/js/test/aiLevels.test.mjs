// Run with: node --test backend/app/web/js/test   (no packages; pytest does not run these)
import test from "node:test";
import assert from "node:assert/strict";
import { draftInsertAllowed } from "../aiLevels.js";

test("a drafted paragraph may be inserted into the manuscript only at the Full AI level (Q27)", () => {
  assert.equal(draftInsertAllowed("full"), true);
  assert.equal(draftInsertAllowed("suggestions_only"), false);
  assert.equal(draftInsertAllowed("off"), false);
  assert.equal(draftInsertAllowed(undefined), false);
});
