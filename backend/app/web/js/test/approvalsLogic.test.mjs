import test from "node:test";
import assert from "node:assert/strict";
import { focusGate, focusStatus, gateGroups, stageProgress } from "../approvalsLogic.js";

const STAGES = [["idea"], ["scoped", "G1"], ["searched", "G2"], ["screened", "G3"]];
const gate = (code, status, extra = {}) => ({ code, status, can_decide: false, waiting_for: [], ...extra });

test("the current stage's own gate is the focus while it is not approved", () => {
  const gates = [gate("G1", "approved"), gate("G2", "pending", { waiting_for: ["G1"] }), gate("G3", "pending", { can_decide: true })];
  assert.equal(focusGate(gates, "searched", STAGES).code, "G2");
});

test("without a gate on the current stage the first decidable gate wins, then the first open one", () => {
  const gates = [gate("G1", "approved"), gate("G2", "pending"), gate("G3", "pending", { can_decide: true })];
  assert.equal(focusGate(gates, "idea", STAGES).code, "G3");
  assert.equal(focusGate([gate("G1", "approved"), gate("G2", "pending")], "idea", STAGES).code, "G2");
  assert.equal(focusGate([gate("G1", "approved")], "scoped", STAGES), null);
});

test("groups never drop a gate and rejected gates stay out of the collapsed ones", () => {
  const gates = [gate("G1", "approved"), gate("G2", "rejected"), gate("G3", "pending", { can_decide: true }), gate("G4", "pending")];
  const groups = gateGroups(gates, "screened", STAGES);
  assert.equal(groups.focus.code, "G3");
  assert.deepEqual(groups.attention.map((g) => g.code), ["G2"]);
  assert.deepEqual(groups.done.map((g) => g.code), ["G1"]);
  assert.deepEqual(groups.later.map((g) => g.code), ["G4"]);
  assert.equal(1 + groups.attention.length + groups.done.length + groups.later.length, gates.length);
});

test("the focus sentence names what the gate waits for and everything is approved reads as such", () => {
  assert.equal(focusStatus(gate("G3", "pending", { can_decide: true })), "Waiting for your decision.");
  assert.equal(focusStatus(gate("G3", "pending", { waiting_for: ["G2", "ethics"] })), "Not open yet: waits for G2, ethics.");
  assert.equal(focusStatus(null), "Every gate is approved.");
  assert.deepEqual(stageProgress(STAGES, "searched"), { done: 2, total: 4, currentIndex: 2 });
});
