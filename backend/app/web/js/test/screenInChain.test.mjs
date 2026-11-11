import test from "node:test";
import assert from "node:assert/strict";
import { SCREEN_STAGE, screenInChain, STEPS } from "../workflow.js";
import { previousStep } from "../steps/frame.js";

const stages = (names) => names.map((stage) => ({ stage, gate: null, label: null }));
const LITERATURE_REVIEW = stages([
  "idea", "scoped", "design_approved", "search_planned", "retrieved", "screened", "extracted", "synthesized",
  "gaps_selected", "drafted", "revised", "submission_ready", "submitted", "revision_loop", "accepted",
]);
const QUALITATIVE = stages([
  "idea", "scoped", "search_planned", "retrieved", "screened", "extracted", "synthesized", "gaps_selected", "framework",
  "design_approved", "data_collected", "analyzed", "drafted", "revised", "submission_ready", "submitted", "revision_loop", "accepted",
]);
const WITHOUT_SYNTHESIS = LITERATURE_REVIEW.filter((item) => item.stage !== "synthesized");

test("every screen with a stage is part of literature_review and qualitative, which keep those stages", () => {
  for (const chain of [LITERATURE_REVIEW, QUALITATIVE]) {
    for (const stepId of Object.keys(SCREEN_STAGE)) assert.equal(screenInChain(stepId, chain), true, stepId);
  }
});

test("a screen whose stage the type lacks is not part of the type; others stay", () => {
  assert.equal(screenInChain("ask", WITHOUT_SYNTHESIS), false);
  assert.equal(screenInChain("write", WITHOUT_SYNTHESIS), true);
});
test("Gaps belongs to a chain with either G5 or G6 and disappears when neither is present", () => {
  assert.equal(screenInChain("gaps", stages(["gaps_selected"])), true);
  assert.equal(screenInChain("gaps", stages(["framework"])), true);
  assert.equal(screenInChain("gaps", stages(["idea", "scoped"])), false);
});

test("Previous step uses Gaps when the chain includes either supported stage", () => {
  assert.equal(previousStep("ask", LITERATURE_REVIEW).id, "gaps");
  assert.equal(previousStep("ask", WITHOUT_SYNTHESIS).id, "gaps");
});

test("screens with no stage of their own belong to every type, and a missing chain hides nothing", () => {
  for (const stepId of ["overview", "guide", "approvals", "team", "activity"]) assert.equal(screenInChain(stepId, WITHOUT_SYNTHESIS), true);
  assert.equal(screenInChain("ask", []), true);
  assert.equal(screenInChain("ask", undefined), true);
});

test("every SCREEN_STAGE key is a real screen", () => {
  const ids = new Set(STEPS.map((step) => step.id));
  for (const stepId of Object.keys(SCREEN_STAGE)) assert.ok(ids.has(stepId), stepId);
});

test("Previous step skips a screen the type does not have", () => {
  assert.equal(previousStep("write", LITERATURE_REVIEW).id, "ask");
  assert.equal(previousStep("write", WITHOUT_SYNTHESIS).id, "gaps");
});
