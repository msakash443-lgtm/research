import test from "node:test";
import assert from "node:assert/strict";
import { resolveConflict } from "../offlineOutbox.js";

test("conflict resolver keeps the local capture when the researcher chooses mine", () => {
  const mine = { client_id: "c1", body: "local" };
  const theirs = { client_id: "c1", body: "server" };
  assert.deepEqual(resolveConflict(mine, theirs, "mine"), { action: "retry", payload: mine });
});

test("conflict resolver can discard the local copy", () => {
  const mine = { client_id: "c1", body: "local" };
  const theirs = { client_id: "c1", body: "server" };
  assert.deepEqual(resolveConflict(mine, theirs, "theirs"), { action: "drop", payload: theirs });
});

test("conflict resolver preserves both copies without losing either body", () => {
  const mine = { client_id: "c1", body: "local" };
  const theirs = { client_id: "c1", body: "server" };
  const resolved = resolveConflict(mine, theirs, "both");
  assert.equal(resolved.action, "retry");
  assert.match(resolved.payload.body, /local/);
  assert.match(resolved.payload.body, /server/);
});
