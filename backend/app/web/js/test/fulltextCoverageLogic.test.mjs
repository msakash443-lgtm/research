// Run with: node --test backend/app/web/js/test   (no packages; pytest does not run these)
import test from "node:test";
import assert from "node:assert/strict";
import { coverageSummary } from "../fulltextCoverageLogic.js";

test("each level has its own wording and tone", () => {
  assert.deepEqual(["full_text", "abstract_only", "not_accessible"].map((level) => coverageSummary({ level }).text),
    ["Full text available", "Abstract only", "Not accessible"]);
  assert.deepEqual(["full_text", "abstract_only", "not_accessible"].map((level) => coverageSummary({ level }).tone), ["good", "warn", "bad"]);
});

test("the open-access link and the reason are passed through, but only a plain http(s) link", () => {
  const summary = coverageSummary({ level: "abstract_only", open_access_url: "https://repo.example/p", reason: "no open-access copy was found" });
  assert.equal(summary.link, "https://repo.example/p");
  assert.equal(summary.reason, "no open-access copy was found");
  assert.equal(coverageSummary({ level: "abstract_only", open_access_url: "javascript:alert(1)" }).link, null);
});

test("a missing or unrecognised coverage is shown as unknown, never as full text", () => {
  assert.equal(coverageSummary(undefined).text, "Coverage unknown");
  assert.equal(coverageSummary({ level: "something_new" }).text, "Coverage unknown");
});
