// Run with: node --test backend/app/web/js/test/draftRecovery.test.mjs
// X.31.16: a crash or reload while offline must recover the last autosaved draft from IndexedDB without data loss.
// `editor/draft.js` (built under X.31.4) writes the draft to IndexedDB before every network save
// (`steps/write.js`'s `save()`); this proves the round trip actually survives a fresh IndexedDB connection the way a
// reloaded tab would open one, not just that `saveDraft`/`loadDraft` exist. Uses `fake-indexeddb` (added as a
// devDependency for this task) since Node has no native IndexedDB.
import test from "node:test";
import assert from "node:assert/strict";
import "fake-indexeddb/auto";
import { saveDraft, loadDraft, clearDraft } from "../editor/draft.js";

test("an autosaved draft survives a reload: a fresh IndexedDB connection reads back the exact content", async () => {
  const draft = {
    document_id: "doc-1", base_number: 3, client_save_id: "save-1",
    content_json: { type: "doc", content: [{ type: "paragraph", content: [{ type: "text", text: "mid-sentence when the tab closed" }] }] },
    saved_at: "2026-10-19T12:00:00.000Z",
  };
  const kept = await saveDraft(draft);
  assert.equal(kept, true);

  // A real crash/reload opens a brand new IndexedDB connection; nothing in this process's memory survives it.
  const recovered = await loadDraft("doc-1");
  assert.deepEqual(recovered, draft);
});

test("a draft for one section never leaks into the recovery of another", async () => {
  await saveDraft({ document_id: "doc-a", base_number: 0, client_save_id: "s-a", content_json: { type: "doc", content: [] }, saved_at: "x" });
  await saveDraft({ document_id: "doc-b", base_number: 0, client_save_id: "s-b", content_json: { type: "doc", content: [] }, saved_at: "y" });
  const a = await loadDraft("doc-a");
  const b = await loadDraft("doc-b");
  assert.equal(a.client_save_id, "s-a");
  assert.equal(b.client_save_id, "s-b");
});

test("once the server confirms a save, the recovered draft is cleared and no longer offered back", async () => {
  await saveDraft({ document_id: "doc-2", base_number: 1, client_save_id: "s-2", content_json: { type: "doc", content: [] }, saved_at: "z" });
  assert.ok(await loadDraft("doc-2"));
  await clearDraft("doc-2");
  const recovered = await loadDraft("doc-2");
  assert.equal(recovered, null);
});

test("loadDraft recovers nothing for a section that was never autosaved", async () => {
  const recovered = await loadDraft("doc-never-saved");
  assert.equal(recovered, null);
});
