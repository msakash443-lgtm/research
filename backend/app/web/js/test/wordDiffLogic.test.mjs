import { test } from "node:test";
import assert from "node:assert/strict";
import { wordDiff, withSeparators } from "../wordDiffLogic.js";

test("identical text gives one unchanged segment", () => {
  assert.deepEqual(wordDiff("the cat sat", "the cat sat"), [{ type: "same", text: "the cat sat" }]);
});

test("a replaced word is shown as deleted then inserted, keeping the shared words unchanged", () => {
  assert.deepEqual(wordDiff("the big cat", "the small cat"), [
    { type: "same", text: "the " },
    { type: "del", text: "big" },
    { type: "ins", text: "small" },
    { type: "same", text: " cat" },
  ]);
});

test("pure insertion and deletion are reported as such", () => {
  assert.deepEqual(wordDiff("a b", "a x b"), [
    { type: "same", text: "a " },
    { type: "ins", text: "x " },
    { type: "same", text: "b" },
  ]);
  assert.deepEqual(wordDiff("a x b", "a b"), [
    { type: "same", text: "a " },
    { type: "del", text: "x " },
    { type: "same", text: "b" },
  ]);
});

test("joining the segments in order reproduces the new text exactly, minus deletions", () => {
  const before = "Results were clearly significant in all groups.";
  const after = "Results were significant across all the groups.";
  const segments = wordDiff(before, after);
  const rebuilt = segments.filter((s) => s.type !== "del").map((s) => s.text).join("");
  const original = segments.filter((s) => s.type !== "ins").map((s) => s.text).join("");
  assert.equal(rebuilt, after);
  assert.equal(original, before);
});

test("empty input on one side is one whole insertion or deletion", () => {
  assert.deepEqual(wordDiff("", "new"), [{ type: "ins", text: "new" }]);
  assert.deepEqual(wordDiff("old", ""), [{ type: "del", text: "old" }]);
});

test("a deleted word followed by an inserted word gets a separator so copied text does not run together", () => {
  const segments = withSeparators(wordDiff("the big cat", "the small cat"));
  assert.deepEqual(segments, [
    { type: "same", text: "the " },
    { type: "del", text: "big" },
    { type: "sep", text: " " },
    { type: "ins", text: "small" },
    { type: "same", text: " cat" },
  ]);
});

test("separators are not added where a changed run is not adjacent", () => {
  const segments = withSeparators(wordDiff("a b", "a x b"));
  assert.equal(segments.some((s) => s.type === "sep"), false);
});
