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

import { noteEditItem, rebaseEdit } from "../offlineOutbox.js";

test("an offline note edit is keyed to the revision it was made from", () => {
  const item = noteEditItem({ id: "n1", revision: 3 }, "new text");
  assert.deepEqual(item, { clientId: "edit-n1-3", payload: { body: "new text", base_revision: 3 } });
});

test("a kept edit is rebased onto the server's current revision so the retry can succeed", () => {
  const mine = { body: "mine", base_revision: 3 };
  assert.deepEqual(rebaseEdit(mine, { revision: 5 }), { ...mine, base_revision: 5 });
  assert.deepEqual(rebaseEdit(mine, undefined), mine);
});

// Minimal in-memory IndexedDB: just the calls offlineOutbox.js makes (open/upgrade, put, getAll, delete).
function installFakeIndexedDb() {
  const rows = new Map();
  let nextId = 1;
  const store = {
    createIndex() {},
    put(item) {
      const id = item.id ?? nextId++;
      rows.set(id, { ...item, id });
      return { result: id };
    },
    getAll: () => ({ result: [...rows.values()] }),
    delete(id) {
      rows.delete(id);
      return {};
    },
  };
  globalThis.indexedDB = {
    open() {
      const request = {};
      const db = {
        objectStoreNames: { contains: () => false },
        createObjectStore: () => store,
        close() {},
        transaction() {
          const tx = { objectStore: () => store };
          setTimeout(() => tx.oncomplete?.(), 0);
          return tx;
        },
      };
      setTimeout(() => {
        request.result = db;
        request.onupgradeneeded?.();
        request.onsuccess?.();
      }, 0);
      return request;
    },
  };
}

test("an offline note edit is replayed as PATCH; a 409 rebases it onto the server revision for the next sync", async () => {
  installFakeIndexedDb();
  const { enqueueNoteEdit, flushOutbox, listOutbox } = await import("../offlineOutbox.js");
  assert.equal(await enqueueNoteEdit({ id: "n1", revision: 3 }, "offline words"), true);

  const calls = [];
  const conflict = Object.assign(new Error("changed"), { status: 409, detail: { code: "note_conflict", current: { revision: 5, body: "server words" } } });
  const first = await flushOutbox(async (path, payload, method) => { calls.push([path, payload, method]); throw conflict; }, () => "mine");
  assert.deepEqual(calls[0], ["/api/notes/n1", { body: "offline words", base_revision: 3 }, "PATCH"]);
  assert.equal(first.blocked, 1);
  assert.deepEqual((await listOutbox())[0].payload, { body: "offline words", base_revision: 5 });

  const second = await flushOutbox(async (path, payload, method) => { calls.push([path, payload, method]); return {}; });
  assert.equal(second.sent, 1);
  assert.deepEqual(calls[1][1], { body: "offline words", base_revision: 5 });
  assert.equal((await listOutbox()).length, 0);
});

test("choosing the saved version drops the queued edit", async () => {
  installFakeIndexedDb();
  const { enqueueNoteEdit, flushOutbox, listOutbox } = await import("../offlineOutbox.js");
  await enqueueNoteEdit({ id: "n2", revision: 1 }, "mine");
  const conflict = Object.assign(new Error("changed"), { status: 409, detail: { current: { revision: 2, body: "theirs" } } });
  await flushOutbox(async () => { throw conflict; }, () => "theirs");
  assert.equal((await listOutbox()).length, 0);
});
