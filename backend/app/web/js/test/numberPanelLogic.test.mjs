// Run with: node --test backend/app/web/js/test   (no packages; pytest does not run these)
import test from "node:test";
import assert from "node:assert/strict";
import { matchingNumbers, numberLabel, runLabel, statAttrs } from "../numberPanelLogic.js";

const OPTIONS = [
  { analysis_run_id: "run-1", path: "descriptives.age.mean", value: 34.2 },
  { analysis_run_id: "run-1", path: "descriptives.age.sd", value: 9.333333333 },
  { analysis_run_id: "run-2", path: "correlations.0.r", value: 0.5 },
  { analysis_run_id: "run-2", path: "correlations.0.n", value: 120 },
];

test("matching numbers are sorted by path then run id", () => {
  assert.deepEqual(
    matchingNumbers(OPTIONS).map((o) => o.path),
    ["correlations.0.n", "correlations.0.r", "descriptives.age.mean", "descriptives.age.sd"],
  );
});

test("a query filters by path substring, case-insensitively", () => {
  assert.deepEqual(matchingNumbers(OPTIONS, "AGE").map((o) => o.path), ["descriptives.age.mean", "descriptives.age.sd"]);
});

test("a query also matches the analysis run id", () => {
  assert.deepEqual(matchingNumbers(OPTIONS, "run-2").map((o) => o.path), ["correlations.0.n", "correlations.0.r"]);
});

test("entries missing a path or run id are dropped", () => {
  const dirty = [...OPTIONS, { analysis_run_id: "", path: "x", value: 1 }, { analysis_run_id: "run-3", path: "", value: 1 }, null];
  assert.equal(matchingNumbers(dirty).length, OPTIONS.length);
});

test("an integer value is labelled plainly", () => {
  assert.equal(numberLabel(OPTIONS[3]), "correlations.0.n = 120");
});

test("a float value is labelled to 4 significant decimals, trimmed", () => {
  assert.equal(numberLabel(OPTIONS[0]), "descriptives.age.mean = 34.2");
  assert.equal(numberLabel(OPTIONS[1]), "descriptives.age.sd = 9.3333");
});

test("the run label is the id's first 8 characters", () => {
  assert.equal(runLabel("12345678-abcd-ef00-0000-000000000000"), "12345678");
  assert.equal(runLabel(""), "");
});

test("stat attrs carry exactly the run id and path, nothing else", () => {
  assert.deepEqual(statAttrs(OPTIONS[0]), { analysis_run_id: "run-1", path: "descriptives.age.mean" });
});
