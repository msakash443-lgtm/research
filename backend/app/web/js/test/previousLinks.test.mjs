import test from "node:test";
import assert from "node:assert/strict";
import { previousStep } from "../steps/frame.js";

const expected = [
  ["overview", null],
  ["scope", "overview"],
  ["criteria", "scope"],
  ["search", "criteria"],
  ["results", "search"],
  ["sources", "results"],
  ["screen", "sources"],
  ["evidence", "screen"],
  ["verification", "evidence"],
  ["ask", "verification"],
  ["write", "ask"],
];

test("every built journey step points back to its preceding step", () => {
  expected.forEach(([stepId, previousId]) => {
    const previous = previousStep(stepId);
    assert.equal(previous?.id ?? null, previousId, stepId);
  });
});
