import test from "node:test";
import assert from "node:assert/strict";
import { canChangeResearchType, canSaveResearchType, selectedResearchType, chainRows, researchTypeLockReason } from "../researchTypeLogic.js";

test("only the owner can change the type, and only at idea with no approved gate", () => {
  assert.equal(canChangeResearchType({ isOwner: true, stage: "idea", gates: [{ status: "pending" }] }), true);
  assert.equal(canChangeResearchType({ isOwner: false, stage: "idea", gates: [] }), false);
  assert.equal(canChangeResearchType({ isOwner: true, stage: "scoped", gates: [] }), false);
  assert.equal(canChangeResearchType({ isOwner: true, stage: "idea", gates: [{ status: "approved" }] }), false);
});

test("the picker shows the picked type while choosing, else the saved one", () => {
  assert.equal(selectedResearchType({ saved: "full_journey", picked: "qualitative" }), "qualitative");
  assert.equal(selectedResearchType({ saved: "full_journey", picked: null }), "full_journey");
});

test("save is enabled only for a real change on a changeable project", () => {
  assert.equal(canSaveResearchType({ saved: "full_journey", picked: "qualitative", changeable: true }), true);
  assert.equal(canSaveResearchType({ saved: "full_journey", picked: "full_journey", changeable: true }), false);
  assert.equal(canSaveResearchType({ saved: "full_journey", picked: "qualitative", changeable: false }), false);
});

test("chain rows keep server order, numbering and mark the mandatory gates", () => {
  const rows = chainRows([
    { stage: "idea", gate: null, label: null },
    { stage: "design_approved", gate: "G7", label: "Design, interview guide & ethics" },
    { stage: "data_collected", gate: null, label: null },
    { stage: "plan_locked", gate: "G8", label: null },
  ]);
  assert.deepEqual(rows.map((row) => row.position), [1, 2, 3, 4]);
  assert.deepEqual(rows.map((row) => row.mandatory), [false, true, false, false]);
  assert.equal(rows[1].label, "Design, interview guide & ethics");
});

test("a locked picker says why: owner-only, past idea, or an approved gate; changeable has no reason", () => {
  assert.equal(researchTypeLockReason({ isOwner: true, stage: "idea", gates: [] }), null);
  assert.match(researchTypeLockReason({ isOwner: false, stage: "idea", gates: [] }), /Only the owner/);
  assert.match(researchTypeLockReason({ isOwner: true, stage: "scoped", gates: [] }), /moved past Idea/);
  assert.match(researchTypeLockReason({ isOwner: true, stage: "idea", gates: [{ code: "G2", status: "approved" }] }), /gate G2 is approved/);
});
