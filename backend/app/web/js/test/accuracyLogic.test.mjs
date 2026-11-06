// Run with: node --test backend/app/web/js/test   (no packages; pytest does not run these)
import test from "node:test";
import assert from "node:assert/strict";
import { extractionNotes, extractionTables, hasAnything, pct, screeningLines } from "../accuracyLogic.js";

const stage = { n: 5, skipped_human_maybe: 1, recall: 0.333, recall_not_excluded: 0.667, precision: 0.5, false_exclusions: 1 };
const empty = { n: 0, skipped_human_maybe: 0, recall: null, recall_not_excluded: null, precision: null, false_exclusions: 0 };

test("a missing figure reads as not enough data, never as 0%", () => {
  assert.equal(pct(null), "not enough data");
  assert.equal(pct(undefined), "not enough data");
  assert.equal(pct(0), "0%");
  assert.equal(pct(0.667), "67%");
});

test("a screening stage with decisions gets one sentence, a stage with none is left out", () => {
  const lines = screeningLines({ title_abstract: stage, full_text: empty });
  assert.equal(lines.length, 1);
  assert.equal(lines[0].stage, "Title and abstract");
  assert.match(lines[0].text, /5 paper\(s\) a person decided/);
  assert.match(lines[0].text, /33% \(67% counting "maybe"\)/);
  assert.match(lines[0].text, /you included 50%/);
  assert.match(lines[0].text, /1 paper\(s\) you included were suggested for exclusion/);
  assert.match(lines[0].text, /1 marked "maybe" by you were not counted/);
});

test("extraction rows list critical fields first and show nulls as not enough data", () => {
  const [table] = extractionTables({ by_schema: { "default@1": { papers: 2, fields: {
    limitations: { critical: false, precision: null, recall: 0 },
    sample: { critical: true, precision: 1, recall: 1 },
    key_findings: { critical: true, precision: 0.5, recall: 0.5 },
  } } } });
  assert.equal(table.schema, "default@1");
  assert.deepEqual(table.rows.map((r) => r.field), ["key_findings", "sample", "limitations"]);
  assert.deepEqual(table.rows[2], { field: "limitations", critical: false, precision: "not enough data", recall: "0%" });
});

test("the notes say why an AI extraction is not counted", () => {
  const notes = extractionNotes({ ai_extractions: 4, not_verified_yet: 2, no_ai_original_kept: 1, old_schema: 0, by_schema: {} });
  assert.equal(notes.length, 2);
  assert.match(notes[0], /2 AI extraction\(s\) are not verified/);
  assert.match(notes[1], /before the AI's first values were kept/);
  assert.deepEqual(extractionNotes({ ai_extractions: 0, not_verified_yet: 0, no_ai_original_kept: 0, old_schema: 0, by_schema: {} }), ["No AI extractions in this project yet."]);
});

test("nothing to show when no stage and no schema has data", () => {
  const none = { screening: { title_abstract: empty, full_text: empty }, extraction: { by_schema: {} } };
  assert.equal(hasAnything(none), false);
  assert.equal(hasAnything({ ...none, screening: { title_abstract: stage, full_text: empty } }), true);
});
