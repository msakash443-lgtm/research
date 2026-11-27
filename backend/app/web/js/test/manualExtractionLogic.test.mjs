import assert from "node:assert/strict";
import test from "node:test";

import { buildManualExtractionPayload, manualExtractionError, submitManualExtraction, valueShape } from "../manualExtractionLogic.js";

const stringField = { key: "question", type: "string" };
const objectField = { key: "sample", type: "object", properties: { n: { type: "integer" }, population: { type: "string" } } };

test("manual extraction payload carries values and PDF evidence for scalar and nested fields", () => {
  const payload = buildManualExtractionPayload({
    sourceId: "source-1", schemaName: "default", fields: [
      { field: stringField, value: "What changed?", quote: "What changed?", page: "4", section: "" },
      { field: objectField, value: '{"n":42,"population":"students"}', quote: "42 students", page: "", section: "Participants" },
    ],
  });
  assert.deepEqual(payload, {
    source_id: "source-1", schema_name: "default",
    fields: { question: "What changed?", sample: { n: 42, population: "students" } },
    evidence: { question: { quote: "What changed?", page: 4 }, sample: { quote: "42 students", section: "Participants" } },
  });
  assert.equal(valueShape(objectField), "JSON object: { n: integer, population: string }");
});

test("manual extraction requires evidence for every reported value", () => {
  assert.throws(() => buildManualExtractionPayload({
    sourceId: "source-1", schemaName: "default", fields: [{ field: stringField, value: "Reported", quote: "", page: "", section: "" }],
  }), /exact supporting quote.*page number or section/);
});

test("manual extraction sends explicit not-reported fields as null without evidence", () => {
  const payload = buildManualExtractionPayload({
    sourceId: "source-1", schemaName: "default", fields: [{ field: stringField, notReported: true }],
  });
  assert.deepEqual(payload, { source_id: "source-1", schema_name: "default", fields: { question: null }, evidence: {} });
});

test("manual extraction submits only to the existing human extraction endpoint", async () => {
  const payload = { source_id: "source-1", schema_name: "default", fields: { question: null }, evidence: {} };
  const calls = [];
  await submitManualExtraction(async (...args) => { calls.push(args); return { id: "extraction-1" }; }, "/api/projects/project-1/extractions", payload);
  assert.deepEqual(calls, [["/api/projects/project-1/extractions", { method: "POST", body: JSON.stringify(payload) }]]);
});

test("manual extraction exposes API error messages to the form", () => {
  assert.equal(manualExtractionError(new Error("The quote was not found in the stored PDF")), "The quote was not found in the stored PDF");
  assert.equal(manualExtractionError(), "The manual extraction could not be saved.");
});

test("a 422 whose detail lists problems shows every listed problem, not the generic HTTP message", () => {
  const error = Object.assign(new Error("Something went wrong (HTTP 422, POST /api/projects/p/extractions)."), {
    status: 422, detail: ["sample: quote not found in the stored PDF", "question: page 99 is outside the document"],
  });
  const message = manualExtractionError(error);
  assert.match(message, /sample: quote not found in the stored PDF/);
  assert.match(message, /question: page 99 is outside the document/);
  assert.doesNotMatch(message, /Something went wrong/);
});
