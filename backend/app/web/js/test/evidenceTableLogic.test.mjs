// Run with: node --test backend/app/web/js/test   (no packages; pytest does not run these)
import test from "node:test";
import assert from "node:assert/strict";
import { aiExtractionAction, normalizeOrientation, tableLayout } from "../evidenceTableLogic.js";

const matrix = {
  fields: [{ key: "sample" }, { key: "measures" }],
  papers: [
    { source_id: "p1", cells: { sample: { value: 10 }, measures: { value: "a" } } },
    { source_id: "p2", cells: { sample: { value: 20 }, measures: { value: null } } },
  ],
};

test("by default papers are the rows and fields the columns", () => {
  const layout = tableLayout(matrix);
  assert.deepEqual(layout.headers.map((h) => h.field.key), ["sample", "measures"]);
  assert.deepEqual(layout.rows.map((r) => r.head.paper.source_id), ["p1", "p2"]);
  assert.deepEqual(layout.rows[1].cells.map((c) => c.cell.value), [20, null]);
});

test("flipped, fields are the rows and papers the columns, with each cell still tied to its own paper and field", () => {
  const layout = tableLayout(matrix, "fields");
  assert.deepEqual(layout.headers.map((h) => h.paper.source_id), ["p1", "p2"]);
  assert.deepEqual(layout.rows.map((r) => r.head.field.key), ["sample", "measures"]);
  const measuresRow = layout.rows[1];
  assert.deepEqual(measuresRow.cells.map((c) => [c.paper.source_id, c.field.key, c.cell.value]), [["p1", "measures", "a"], ["p2", "measures", null]]);
});

test("an unknown orientation falls back to papers as rows", () => {
  assert.equal(normalizeOrientation("sideways"), "papers");
  assert.equal(normalizeOrientation(undefined), "papers");
  assert.equal(normalizeOrientation("fields"), "fields");
});

test("AI extraction reports actionable manual-upload guidance when no stored PDF exists", () => {
  const pending = { extraction_id: null, coverage: { level: "metadata", reason: "No DOI is available." } };
  assert.deepEqual(aiExtractionAction(pending, true), {
    enabled: false,
    message: "No DOI is available. Upload a PDF manually or retry the OA fetch.",
  });
  assert.deepEqual(aiExtractionAction({ ...pending, coverage: { level: "full_text" } }, true), {
    enabled: true,
    message: "Queue AI extraction from the stored full-text PDF.",
  });
  assert.equal(aiExtractionAction({ ...pending, extraction_id: "done" }, true), null);
  assert.equal(aiExtractionAction(pending, false), null);
});
