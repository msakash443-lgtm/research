// Run with: node --test backend/app/web/js/test   (no packages; pytest does not run these)
import test from "node:test";
import assert from "node:assert/strict";
import {
  buildBibtexOwnPaperPayload,
  buildManualOwnPaperPayload,
  buildOwnPaperLookupPayload,
  describeOwnPaperLookup,
  describePollBudgetExhausted,
  isLookupPending,
  parseAuthors,
} from "../ownPaperLogic.js";

test("parseAuthors splits on commas and semicolons, trims, drops blanks", () => {
  assert.deepEqual(parseAuthors("Jane Doe, John Smith; A. N. Other"), ["Jane Doe", "John Smith", "A. N. Other"]);
  assert.equal(parseAuthors(""), undefined);
  assert.equal(parseAuthors("  ,  ;  "), undefined);
});

test("the manual payload requires a title and drops blank optional fields", () => {
  const payload = buildManualOwnPaperPayload({ title: "  My Paper  ", doi: "", url: "", year: "", source_type: "article" });
  assert.deepEqual(payload, { title: "My Paper", source_type: "article" });
  assert.throws(() => buildManualOwnPaperPayload({ title: "   " }), /title/i);
});

test("the manual payload keeps given optional fields and parses year/authors", () => {
  const payload = buildManualOwnPaperPayload({
    title: "My Paper", doi: "10.1/x", url: "https://e.org", venue: "J", abstract: "a",
    oa_url: "https://e.org/oa", authors: "Jane Doe, John Smith", year: "2024", source_type: "report",
  });
  assert.deepEqual(payload, {
    title: "My Paper", doi: "10.1/x", url: "https://e.org", venue: "J", abstract: "a",
    oa_url: "https://e.org/oa", source_type: "report", authors: ["Jane Doe", "John Smith"], year: 2024,
  });
});

test("a non-integer year is rejected before the request is made", () => {
  assert.throws(() => buildManualOwnPaperPayload({ title: "T", year: "not-a-year" }), /whole number/i);
});

test("the BibTeX payload requires non-blank text", () => {
  assert.deepEqual(buildBibtexOwnPaperPayload("  @article{x}  "), { bibtex: "@article{x}" });
  assert.throws(() => buildBibtexOwnPaperPayload("   "), /paste/i);
});

test("the lookup payload takes exactly one of DOI or title", () => {
  assert.deepEqual(buildOwnPaperLookupPayload({ doi: " 10.1/x " }), { doi: "10.1/x" });
  assert.deepEqual(buildOwnPaperLookupPayload({ title: " A Title " }), { title: "A Title" });
  assert.throws(() => buildOwnPaperLookupPayload({ doi: "10.1/x", title: "A Title" }), /either a doi or a title/i);
  assert.throws(() => buildOwnPaperLookupPayload({}), /either a doi or a title/i);
});

test("describeOwnPaperLookup reports pending, included, not_found and failed", () => {
  assert.deepEqual(describeOwnPaperLookup(null), { kind: "pending", text: "Looking…" });
  assert.deepEqual(describeOwnPaperLookup({ status: "included" }), { kind: "included", text: "Added to your sources." });
  assert.deepEqual(describeOwnPaperLookup({ status: "not_found", detail: "No match." }), { kind: "not_found", text: "No match." });
  assert.deepEqual(describeOwnPaperLookup({ status: "not_found" }).kind, "not_found");
  assert.deepEqual(describeOwnPaperLookup({ status: "failed", detail: "This DOI is already in the project." }), {
    kind: "failed", text: "This DOI is already in the project.",
  });
});

test("isLookupPending is true only for a 404 (not resolved yet)", () => {
  assert.equal(isLookupPending({ status: 404 }), true);
  assert.equal(isLookupPending({ status: 500 }), false);
  assert.equal(isLookupPending(null), false);
});

test("a lookup still unresolved when the poll budget runs out is pending, not failed, and can be checked again (M2.14.2)", () => {
  const state = describePollBudgetExhausted("task-1");

  assert.equal(state.kind, "pending");
  assert.equal(state.taskId, "task-1");
  assert.ok(!/check back later/i.test(state.text));
  assert.ok(state.text.includes("Check again"));
});
