// Run with: node --test backend/app/web/js/test/conflictLogic.test.mjs
// X.31.16: the writing editor's compare view when a save's base_number is behind the server's latest
// (`routers/documents.py`'s 409 `version_conflict`). Also exercises the first real call into offlineOutbox.js's
// `resolveConflict` (X.31.5), closing the "dead code" review finding as a side effect of this task.
import test from "node:test";
import assert from "node:assert/strict";
import { textOf, planConflictResolution } from "../editor/conflictLogic.js";

function doc(...lines) {
  return { type: "doc", content: lines.map((text) => ({ type: "paragraph", content: text ? [{ type: "text", text }] : [] })) };
}

test("textOf reads the plain text of a saved content_json", () => {
  assert.equal(textOf(doc("first line", "second line")), "first line\nsecond line");
});

test("textOf never throws on unreadable content", () => {
  assert.equal(textOf({ type: "doc", content: [{ type: "not_a_real_node" }] }), "");
  assert.equal(textOf(null), "");
});

test("keep-mine retries the save with the researcher's own content, untouched", () => {
  const mine = doc("my unsaved words");
  const theirs = doc("someone else's save");
  const plan = planConflictResolution(mine, theirs, "mine");
  assert.equal(plan.action, "retry");
  assert.deepEqual(plan.content_json, mine);
});

test("keep-theirs drops the local draft and shows the server's content", () => {
  const mine = doc("my unsaved words");
  const theirs = doc("someone else's save");
  const plan = planConflictResolution(mine, theirs, "theirs");
  assert.equal(plan.action, "drop");
  assert.deepEqual(plan.content_json, theirs);
});

test("keep-both merges without losing either side's words, and saves the merge", () => {
  const mine = doc("my unsaved words");
  const theirs = doc("someone else's save");
  const plan = planConflictResolution(mine, theirs, "both");
  assert.equal(plan.action, "retry");
  const merged = textOf(plan.content_json);
  assert.match(merged, /my unsaved words/);
  assert.match(merged, /someone else's save/);
});

test("an unknown choice throws instead of silently keeping nobody's work", () => {
  assert.throws(() => planConflictResolution(doc("a"), doc("b"), "neither"));
});
