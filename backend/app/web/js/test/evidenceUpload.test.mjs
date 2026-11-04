import test from "node:test";
import assert from "node:assert/strict";
import { uploadFulltext } from "../steps/evidence.js";

test("uploading a selected PDF sends raw PDF bytes to the paper's guarded full-text endpoint", async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (path, options) => {
    calls.push({ path, options });
    return new Response(JSON.stringify({ fulltext_path: "projects/paper.pdf" }), { status: 201 });
  };
  const bytes = new TextEncoder().encode("%PDF-1.4 example").buffer;
  const file = { type: "application/pdf", arrayBuffer: async () => bytes };

  try {
    const result = await uploadFulltext({ project: { id: "project-1" } }, { source_id: "paper-1" }, file);
    assert.equal(result.fulltext_path, "projects/paper.pdf");
    assert.equal(calls.length, 1);
    assert.equal(calls[0].path, "/api/projects/project-1/sources/paper-1/fulltext/upload");
    assert.equal(calls[0].options.method, "POST");
    assert.equal(calls[0].options.headers["Content-Type"], "application/pdf");
    assert.equal(calls[0].options.body, bytes);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("uploading without a PDF file is rejected before making a request", async () => {
  await assert.rejects(
    uploadFulltext({ project: { id: "project-1" } }, { source_id: "paper-1" }, null),
    /Choose a PDF file first/,
  );
});
