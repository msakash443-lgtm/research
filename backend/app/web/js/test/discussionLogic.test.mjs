import test from "node:test";
import assert from "node:assert/strict";
import { discussionAccess, noteBody, rulingRequest, threadLines } from "../steps/discussionLogic.js";

const decisions = [{ decider: "u1" }, { decider: "u2" }];
const note = (author, current = true) => ({ id: author + current, author, body: "b", created_at: "t", current });

test("only a reviewer who holds a decision may write in an open disagreement", () => {
  const base = { decisions, canWrite: true };
  assert.equal(discussionAccess({ ...base, userId: "u1" }).canNote, true);
  assert.equal(discussionAccess({ ...base, userId: "u3" }).canNote, false);
  assert.equal(discussionAccess({ ...base, userId: "u1", ruled: true }).canNote, false);
  assert.equal(discussionAccess({ ...base, userId: "u1", locked: true }).canNote, false);
  assert.equal(discussionAccess({ ...base, userId: "u1", canWrite: false }).canNote, false);
});

test("the ruling opens only for an independent owner or supervisor after a current discussion", () => {
  const access = (extra) => discussionAccess({ userId: "u3", decisions, canRule: true, ...extra }).ruling;
  assert.equal(access({ notes: [note("u1")] }).state, "open");
  assert.equal(access({ notes: [] }).state, "needs_discussion");
  assert.equal(access({ notes: [note("u1", false)] }).state, "needs_discussion");
  assert.equal(access({ notes: [note("u1"), note("u3")] }).state, "took_part");
  assert.equal(access({ userId: "u1", notes: [note("u1")] }).state, "decided_myself");
  assert.equal(access({ notes: [note("u1")], locked: true }).state, "locked");
  assert.equal(access({ canRule: false, notes: [note("u1")] }).state, "hidden");
  assert.match(access({ notes: [] }).message, /discuss this disagreement first/);
});

test("a ruling is built only with a written rationale, and an exclusion also names its criterion", () => {
  const base = { sourceId: "s1", stage: "title_abstract", decision: "include", reasonCode: "", rationale: "  Methods match I2.  " };
  assert.deepEqual(rulingRequest(base), { ok: true, body: { source_id: "s1", stage: "title_abstract", decision: "include", reason_code: null, note: "Methods match I2." } });
  assert.equal(rulingRequest({ ...base, rationale: "   " }).ok, false);
  assert.match(rulingRequest({ ...base, decision: "exclude" }).message, /reason code/);
  assert.equal(rulingRequest({ ...base, decision: "exclude", reasonCode: "E_OTHER" }).ok, true);
  assert.equal(rulingRequest({ ...base, reasonCode: "E_OTHER" }).ok, false);
});

test("thread lines name the author, keep order and mark notes from before a changed answer", () => {
  const lines = threadLines([note("u1", false), note("u2")], new Map([["u1", "Priya"]]));
  assert.deepEqual(lines.map((l) => [l.who, l.current]), [["Priya", false], ["u2", true]]);
  assert.deepEqual(noteBody("  hi "), { ok: true, body: "hi" });
  assert.equal(noteBody(" ").ok, false);
});
