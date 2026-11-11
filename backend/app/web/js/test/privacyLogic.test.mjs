import { test } from "node:test";
import assert from "node:assert/strict";
import { parsePrivacyRoute, confirmEmailMatches, processorRows } from "../privacyLogic.js";

test("the privacy hash route matches with or without a trailing slash and nothing else", () => {
  assert.equal(parsePrivacyRoute("#/privacy"), true);
  assert.equal(parsePrivacyRoute("#/privacy/"), true);
  assert.equal(parsePrivacyRoute("#/inbox"), false);
  assert.equal(parsePrivacyRoute("#/p/1/overview"), false);
  assert.equal(parsePrivacyRoute("#/privacy/extra"), false);
  assert.equal(parsePrivacyRoute(""), false);
  assert.equal(parsePrivacyRoute(undefined), false);
});

test("erasure is confirmed only when the typed email equals the account email ignoring case and surrounding spaces", () => {
  assert.equal(confirmEmailMatches("Ada@Example.test", "ada@example.test"), true);
  assert.equal(confirmEmailMatches("  ada@example.test  ", "ada@example.test"), true);
  assert.equal(confirmEmailMatches("grace@example.test", "ada@example.test"), false);
  assert.equal(confirmEmailMatches("", "ada@example.test"), false);
  assert.equal(confirmEmailMatches("ada@example.test", ""), false);
  assert.equal(confirmEmailMatches("ada@example.test", null), false);
  assert.equal(confirmEmailMatches(null, "ada@example.test"), false);
});

test("processor rows map the server fields and show a dash when the host is missing", () => {
  const rows = processorRows([
    { id: "openalex", label: "OpenAlex", purpose: "Search", data_sent: "Search terms", host: "api.openalex.org" },
    { id: "llm", label: "Language model", purpose: "Drafting", data_sent: "Excerpts", host: "" },
  ]);
  assert.deepEqual(rows[0], {
    key: "openalex",
    label: "OpenAlex",
    purpose: "Search",
    dataSent: "Search terms",
    host: "api.openalex.org",
  });
  assert.equal(rows[1].host, "—");
});

test("processor rows are empty for a missing or non-list payload", () => {
  assert.deepEqual(processorRows(undefined), []);
  assert.deepEqual(processorRows({}), []);
});
