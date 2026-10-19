import test from "node:test";
import assert from "node:assert/strict";
import { STAGES, setStageChain, stageLabel, NO_SCREEN_STAGES, stagesWithoutScreen } from "../workflow.js";

test("setStageChain carries the research type's own per-stage label through", () => {
  setStageChain([
    { stage: "idea", gate: null, label: null },
    { stage: "framework", gate: "G6", label: "Sensitising concepts & questions" },
    { stage: "design_approved", gate: "G7", label: "Design, interview guide & ethics" },
  ]);
  assert.equal(stageLabel(STAGES[0]), "Idea");
  assert.equal(stageLabel(STAGES[1]), "Sensitising concepts & questions");
  assert.equal(stageLabel(STAGES[2]), "Design, interview guide & ethics");
});

test("stageLabel falls back to the generic snake_case -> Title Case conversion when no override is set", () => {
  assert.equal(stageLabel(["design_approved", "G7", null]), "Design Approved");
  assert.equal(stageLabel(["data_collected"]), "Data Collected");
});

test("stageLabel returns an empty string for a missing entry", () => {
  assert.equal(stageLabel(undefined), "");
});

// full_journey.toml (19 stages, no stage carries its own label override): the literal chain shape GET /stage
// returns for every pre-existing project. Mirrors today's index-slice-from-"retrieved" behaviour so the
// membership-based filter in app.js stays a byte-identical replacement for that hard-coded anchor.
const FULL_JOURNEY_CHAIN = [
  { stage: "idea", gate: null, label: null },
  { stage: "scoped", gate: "G1", label: null },
  { stage: "search_planned", gate: "G2", label: null },
  { stage: "retrieved", gate: null, label: null },
  { stage: "screened", gate: "G3", label: null },
  { stage: "extracted", gate: "G4", label: null },
  { stage: "synthesized", gate: null, label: null },
  { stage: "gaps_selected", gate: "G5", label: null },
  { stage: "framework", gate: "G6", label: null },
  { stage: "design_approved", gate: "G7", label: null },
  { stage: "data_collected", gate: null, label: null },
  { stage: "plan_locked", gate: "G8", label: null },
  { stage: "analyzed", gate: "G9", label: null },
  { stage: "drafted", gate: "G10", label: null },
  { stage: "revised", gate: null, label: null },
  { stage: "submission_ready", gate: "G11", label: null },
  { stage: "submitted", gate: null, label: null },
  { stage: "revision_loop", gate: null, label: null },
  { stage: "accepted", gate: null, label: null },
];

function oldIndexSliceFromRetrieved(chain) {
  // The exact behaviour app.js::renderShell() used to compute before this change: a hard-coded anchor on the
  // "retrieved" stage name, sliced to the end of the chain.
  const fromIndex = chain.findIndex(([name]) => name === "retrieved");
  return chain.slice(fromIndex);
}

test("the no-screen stage filter matches today's index-slice-from-\"retrieved\" later-stages set, minus the stages that now have a dedicated screen", () => {
  setStageChain(FULL_JOURNEY_CHAIN);
  const oldBehaviour = oldIndexSliceFromRetrieved(STAGES).map(([name]) => name);
  const newBehaviour = stagesWithoutScreen().map(([name]) => name);

  // Every stage the new filter returns was present in the old hard-coded slice, in the same relative order.
  assert.deepEqual(
    newBehaviour,
    oldBehaviour.filter((name) => NO_SCREEN_STAGES.has(name))
  );
  // The three stages that gained a dedicated screen (screen, evidence/verification, write) since the old
  // "retrieved" anchor was written are the only ones now excluded from the "Later stages" box.
  assert.deepEqual(
    oldBehaviour.filter((name) => !NO_SCREEN_STAGES.has(name)),
    ["screened", "extracted", "drafted"]
  );
});

test("stagesWithoutScreen preserves the project's own chain order for a reordered/shortened research type", () => {
  setStageChain([
    { stage: "idea", gate: null, label: null },
    { stage: "accepted", gate: null, label: null },
    { stage: "retrieved", gate: null, label: null },
  ]);
  assert.deepEqual(
    stagesWithoutScreen().map(([name]) => name),
    ["accepted", "retrieved"]
  );
});
