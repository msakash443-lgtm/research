import test from "node:test";
import assert from "node:assert/strict";
import { comparisonGroups, directionText, gapPrefill, saveGapPrefill, skippedText, takeGapPrefill } from "../comparisonLogic.js";

const f = (id, title, direction) => ({ source_id: id, title, direction, quote: `q ${id}`, page: 2 });
const result = {
  unstructured_findings: 2,
  conflicts: [{ exposure: "Training", outcome: "Retention", population_note: "may differ by population", findings: [f("a", "A", "positive"), f("b", "B", "negative")] }],
  inconsistent: [{ exposure: "Training", outcome: "Wages", findings: [f("a", "A", "positive"), f("c", "C", null)] }],
  agreements: [{ exposure: "Training", outcome: "Hours", findings: [f("a", "A", "positive"), f("b", "B", "positive"), f("a", "A", "positive")] }],
};

test("groups come out conflicts first, then inconsistent, then agreements, filtered by kind", () => {
  assert.deepEqual(comparisonGroups(result).map((g) => g.kind), ["conflicts", "inconsistent", "agreements"]);
  assert.deepEqual(comparisonGroups(result, ["agreements"]).map((g) => g.outcome), ["Hours"]);
  assert.equal(comparisonGroups(result)[0].populationNote, "may differ by population");
  assert.equal(comparisonGroups(result)[1].populationNote, null);
  assert.deepEqual(comparisonGroups(null), []);
});

test("direction null reads as no effect reported, and skipped findings are counted in words", () => {
  assert.equal(directionText(null), "No effect reported");
  assert.equal(directionText("negative"), "Effect: decrease");
  assert.match(skippedText(result), /^2 findings skipped/);
  assert.match(skippedText({ unstructured_findings: 1 }), /^1 finding skipped/);
  assert.equal(skippedText({}), "");
});

test("a gap draft lists each paper once and names the disagreement or agreement", () => {
  const conflict = gapPrefill(comparisonGroups(result)[0]);
  assert.deepEqual(conflict.supportingPaperIds, ["a", "b"]);
  assert.match(conflict.statement, /disagree on the effect of Training on Retention: A \(increase\); B \(decrease\)\./);
  const agreement = gapPrefill(comparisonGroups(result)[2]);
  assert.deepEqual(agreement.supportingPaperIds, ["a", "b"]);
  assert.match(agreement.statement, /^Studies agree on/);
});

test("the gap prefill is read once, per project, and a corrupt value is ignored", () => {
  const data = new Map();
  const storage = { getItem: (k) => data.get(k) ?? null, setItem: (k, v) => data.set(k, v), removeItem: (k) => data.delete(k) };
  saveGapPrefill(storage, "p1", { type: "empirical", statement: "S", supportingPaperIds: ["a", "b"] });
  assert.equal(takeGapPrefill(storage, "p2"), null);
  assert.deepEqual(takeGapPrefill(storage, "p1").supportingPaperIds, ["a", "b"]);
  assert.equal(takeGapPrefill(storage, "p1"), null);
  data.set("gap-prefill:p1", "{not json");
  assert.equal(takeGapPrefill(storage, "p1"), null);
});
