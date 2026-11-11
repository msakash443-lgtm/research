// Run with: node --test backend/app/web/js/test/manuscriptExportLogic.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { manuscriptExportItems } from "../manuscriptExportLogic.js";

test("the menu offers Markdown, Word, JSON and BibTeX in that order", () => {
  const items = manuscriptExportItems("p1");
  assert.deepEqual(items.map((item) => item.label), ["Markdown", "Word (DOCX)", "JSON", "BibTeX (verified sources)"]);
});

test("each item links to the manuscript export for the project with its format", () => {
  const urls = manuscriptExportItems("p1").map((item) => item.href);
  assert.deepEqual(urls, [
    "/api/projects/p1/manuscript/export?format=md",
    "/api/projects/p1/manuscript/export?format=docx",
    "/api/projects/p1/manuscript/export?format=json",
    "/api/projects/p1/manuscript/export?format=bibtex",
  ]);
});

test("a project id with odd characters is encoded", () => {
  assert.equal(manuscriptExportItems("a/b")[0].href, "/api/projects/a%2Fb/manuscript/export?format=md");
});
