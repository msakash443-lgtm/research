import test from "node:test";
import assert from "node:assert/strict";
import { CREDIT_ROLES, assignmentsAfter, creditSelection, levelOf, matrixRows } from "../teamLogic.js";

test("the CRediT taxonomy has its 14 standard terms and a selection keeps taxonomy order without duplicates", () => {
  assert.equal(CREDIT_ROLES.length, 14);
  const picked = creditSelection(["Supervision"], "Conceptualization", true);
  assert.deepEqual(picked, ["Conceptualization", "Supervision"]);
  assert.deepEqual(creditSelection(picked, "Supervision", true), picked);
  assert.deepEqual(creditSelection(picked, "Conceptualization", false), ["Supervision"]);
  assert.deepEqual(creditSelection(undefined, "Software", true), ["Software"]);
});

const matrix = {
  areas: [{ code: "screening", label: "Screening" }, { code: "writing", label: "Writing" }],
  assignments: [{ area: "screening", user_id: "u1", level: "lead" }, { area: "screening", user_id: "u2", level: "support" }],
};

test("rows hold each area's lead and supporters, and an unassigned area is empty", () => {
  const rows = matrixRows(matrix);
  assert.deepEqual(rows[0], { code: "screening", label: "Screening", lead: "u1", support: ["u2"] });
  assert.deepEqual(rows[1], { code: "writing", label: "Writing", lead: null, support: [] });
  assert.deepEqual(["u1", "u2", "u3"].map((id) => levelOf(rows[0], id)), ["lead", "support", ""]);
});

test("changing one person's level rewrites the whole area: one lead, the old lead stays as support", () => {
  const row = matrixRows(matrix)[0];
  assert.deepEqual(assignmentsAfter(row, "u3", "lead"), [
    { user_id: "u3", level: "lead" }, { user_id: "u2", level: "support" }, { user_id: "u1", level: "support" }]);
  assert.deepEqual(assignmentsAfter(row, "u1", ""), [{ user_id: "u2", level: "support" }]);
  assert.deepEqual(assignmentsAfter(row, "u2", "lead"), [{ user_id: "u2", level: "lead" }, { user_id: "u1", level: "support" }]);
  assert.deepEqual(assignmentsAfter(row, "u1", "lead"), [{ user_id: "u1", level: "lead" }, { user_id: "u2", level: "support" }]);
});
