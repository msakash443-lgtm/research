// Run with: node --test backend/app/web/js/test   (no packages; pytest does not run these)
import test from "node:test";
import assert from "node:assert/strict";
import { filterByCitations, rankByCitations } from "../citationSortFilterLogic.js";

const COUNT_OF = (item) => item.citation_count;

const ITEMS = [
  { id: "a", citation_count: 10 },
  { id: "b", citation_count: null },
  { id: "c", citation_count: 40 },
  { id: "d", citation_count: 2 },
  { id: "e", citation_count: null },
  { id: "f", citation_count: 40 }, // ties with c; input order breaks the tie
];

test("no threshold: nothing is hidden, unknowns are counted but kept in place", () => {
  const result = filterByCitations(ITEMS, COUNT_OF, null);
  assert.deepEqual(result.shown.map((r) => r.item.id), ["a", "b", "c", "d", "e", "f"]);
  assert.deepEqual(result.shown.map((r) => r.unknown), [false, true, false, false, true, false]);
  assert.equal(result.total, 6);
  assert.equal(result.hiddenBelowThreshold, 0);
  assert.equal(result.unknownCount, 2);
});

test("a threshold hides known counts below it but never hides unknown counts", () => {
  const result = filterByCitations(ITEMS, COUNT_OF, 10);
  assert.deepEqual(result.shown.map((r) => r.item.id), ["a", "b", "c", "e", "f"]);
  assert.equal(result.hiddenBelowThreshold, 1); // "d" (2 < 10)
  assert.equal(result.unknownCount, 2);
  assert.equal(result.total, 6);
});

test("threshold 0 hides nothing (every known count is non-negative)", () => {
  const result = filterByCitations(ITEMS, COUNT_OF, 0);
  assert.equal(result.hiddenBelowThreshold, 0);
  assert.equal(result.shown.length, 6);
});

test("rankByCitations sorts the highest known count first, unknowns last, ties keep input order", () => {
  const result = rankByCitations(ITEMS, COUNT_OF, null);
  assert.deepEqual(result.shown.map((r) => r.item.id), ["c", "f", "a", "d", "b", "e"]);
});

test("rankByCitations applies the threshold before sorting", () => {
  const result = rankByCitations(ITEMS, COUNT_OF, 10);
  assert.deepEqual(result.shown.map((r) => r.item.id), ["c", "f", "a", "b", "e"]);
  assert.equal(result.hiddenBelowThreshold, 1);
  assert.equal(result.unknownCount, 2);
});

test("an empty list is handled without throwing", () => {
  assert.deepEqual(filterByCitations([], COUNT_OF, 5), { shown: [], total: 0, hiddenBelowThreshold: 0, unknownCount: 0 });
  assert.deepEqual(rankByCitations(null, COUNT_OF, 5).shown, []);
});
