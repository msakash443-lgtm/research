// Run with: node --test backend/app/web/js/test   (no packages; pytest does not run these)
import test from "node:test";
import assert from "node:assert/strict";
import { evidenceLine, canAskAgain, suggestionPanelTitle } from "../suggestionLogic.js";

test("an extraction span shows its quote with page and section, not JSON", () => {
  const line = evidenceLine({ quote: "n = 120", page: 4, section: "Methods" });
  assert.equal(line, "page 4, section Methods: “n = 120”");
  assert.ok(!line.includes("{"));
});

test("source-based and string evidence keep their old form", () => {
  assert.equal(evidenceLine("plain"), "plain");
  assert.equal(evidenceLine({ source_id: "s1", quote: "q" }), "s1: “q”");
  assert.equal(evidenceLine({ quote: "q", page: null, section: null }), "“q”");
});

test("a screening decision item shows the suggested decision, not the fallback", () => {
  assert.equal(evidenceLine({ decision: "include", reason_code: "C1" }), "Suggested decision: Include (C1)");
  assert.equal(evidenceLine({ decision: "exclude", reason_code: null }), "Suggested decision: Exclude");
});

test("unknown shapes never print raw JSON", () => {
  assert.ok(!evidenceLine({ x: 1 }).includes("{"));
});

test("Ask again is offered only when the server says a producer exists for the kind (X.96, X.98)", () => {
  assert.equal(canAskAgain({ target_kind: "screening", can_ask_again: false }), false);
  assert.equal(canAskAgain({ target_kind: "extraction", can_ask_again: true }), true);
});

test("a suggestion without the server's answer does not offer Ask again", () => {
  assert.equal(canAskAgain({ target_kind: "note" }), false);
});

test("accepted writing suggestions no longer use the pending panel label", () => {
  assert.equal(suggestionPanelTitle([{ state: "pending" }]), "AI suggestion, not yet yours");
  assert.equal(suggestionPanelTitle([{ state: "accepted" }]), "AI suggestions");
  assert.equal(suggestionPanelTitle([{ state: "pending" }, { state: "accepted" }]), "AI suggestions");
});
