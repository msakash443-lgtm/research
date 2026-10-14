// Run with: node --test backend/app/web/js/test/provenanceReportLogic.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import {
  aiLines,
  coverageLine,
  exportUrl,
  pastedLines,
  shareRows,
  staleNotice,
} from "../provenanceReportLogic.js";

const SHARES = { typed: 91.2, quoted: 4.1, pasted_own: 2.1, pasted_unresolved: 0, ai: 2.6 };

test("shares come out in the design's order with one decimal place", () => {
  assert.deepEqual(shareRows(SHARES), [
    { key: "typed", label: "Typed by authors", pct: "91.2%" },
    { key: "quoted", label: "Quoted (with citation)", pct: "4.1%" },
    { key: "pasted_own", label: "Pasted, confirmed own", pct: "2.1%" },
    { key: "pasted_unresolved", label: "Pasted, unresolved", pct: "0.0%" },
    { key: "ai", label: "AI-edited, accepted", pct: "2.6%" },
  ]);
});

test("a missing share counts as zero rather than NaN", () => {
  const rows = shareRows({ typed: 100 });
  assert.equal(rows.find((row) => row.key === "ai").pct, "0.0%");
});

test("coverage names each covered section with its version number", () => {
  assert.equal(
    coverageLine([
      { title: "Introduction", number: 3 },
      { title: "Methods", number: 5 },
    ]),
    "Covers 2 sections: Introduction (version 3), Methods (version 5)",
  );
});

test("one covered section reads as a singular", () => {
  assert.equal(coverageLine([{ title: "Methods", number: 5 }]), "Covers 1 section: Methods (version 5)");
});

test("a report that is up to date has no stale notice", () => {
  assert.equal(staleNotice({ stale: false, stale_reason: null }), null);
});

test("a stale report says it is out of date and what to do", () => {
  assert.deepEqual(staleNotice({ stale: true, stale_reason: "section changed after the report" }), {
    badge: "Out of date",
    detail: "A covered section changed after this report was made. Make a new report to include the change.",
  });
});

test("export links point at the report's markdown or json form", () => {
  assert.equal(exportUrl("p1", "r9", "md"), "/api/projects/p1/provenance-reports/r9?format=md");
  assert.equal(exportUrl("p1", "r9", "json"), "/api/projects/p1/provenance-reports/r9?format=json");
});

test("any other export format is refused rather than guessed", () => {
  assert.throws(() => exportUrl("p1", "r9", "docx"), /format/);
});

test("AI contributions show the action, model, prompt and kept characters", () => {
  assert.deepEqual(
    aiLines([{ action: "clearer_sentence", model_id: "m-1", prompt_version: "workspace_clearer_sentence@1", chars_kept: 42 }]),
    [{ label: "Clearer sentence", model: "m-1", prompt: "workspace_clearer_sentence@1", kept: "42 characters kept" }],
  );
});

test("an action with no known label is shown as the raw name, and a missing model as not recorded", () => {
  const [row] = aiLines([{ action: "new_thing", model_id: null, prompt_version: "p@1", chars_kept: 0 }]);
  assert.equal(row.label, "new_thing");
  assert.equal(row.model, "not recorded");
});

test("pasted material shows its status, size and source, never its text", () => {
  assert.deepEqual(
    pastedLines([{ status: "quotation", chars: 120, source_id: "s-7" }, { status: "unconfirmed", chars: 30, source_id: null }]),
    [
      { status: "Quotation", chars: "120 characters", source: "source s-7" },
      { status: "Unconfirmed paste", chars: "30 characters", source: "no source linked" },
    ],
  );
});
