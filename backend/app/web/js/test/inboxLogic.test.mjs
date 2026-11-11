// Run with: node --test backend/app/web/js/test   (no packages; pytest does not run these)
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import {
  decisionRows,
  loadDismissed,
  newestRender,
  parseInboxRoute,
  promoteDefaults,
  promotedInto,
  rememberDismissed,
  suggestionChips,
  writableProjects,
  outboxSyncChangedNotes,
  inboxRefreshAfterSync,
} from "../inboxLogic.js";

test("only projects the caller owns or co-authors can receive a filed note", () => {
  const projects = [
    { id: "p1", title: "Mine", owner_id: "me" },
    { id: "p2", title: "Co-written", owner_id: "them" },
    { id: "p3", title: "Reviewing only", owner_id: "them" },
    { id: "p4", title: "Supervised", owner_id: "them" },
  ];
  const members = {
    p2: [{ user_id: "me", role: "co_author" }],
    p3: [{ user_id: "me", role: "reviewer" }],
    p4: [{ user_id: "me", role: "supervisor" }],
  };

  assert.deepEqual(writableProjects(projects, members, "me"), [
    { id: "p1", title: "Mine" },
    { id: "p2", title: "Co-written" },
  ]);
});

test("project, stage and source suggestions all file the note to the project they name", () => {
  const suggestions = [
    { kind: "project", target_id: "p1", label: "Gender wage inequality", score: 0.8, project_id: "p1" },
    { kind: "stage", target_id: "p1", label: "scoped", score: 0.8, project_id: "p1" },
    { kind: "source", target_id: "s1", label: "Sen 2019", score: 0.4, project_id: "p2" },
  ];

  assert.deepEqual(suggestionChips(suggestions), [
    { key: "project:p1", text: "Gender wage inequality", actionable: true, projectId: "p1" },
    { key: "stage:p1", text: "Stage: scoped", actionable: true, projectId: "p1" },
    { key: "source:s1", text: "Source: Sen 2019", actionable: true, projectId: "p2" },
  ]);
});

test("a dismissed suggestion is hidden", () => {
  const suggestions = [{ kind: "project", target_id: "p1", label: "Gender wage", score: 0.5, project_id: "p1" }];

  assert.deepEqual(suggestionChips(suggestions, new Set(["project:p1"])), []);
});

// A fake storage object with the same get/set/remove methods as localStorage.
function fakeStorage() {
  const data = new Map();
  return { getItem: (key) => (data.has(key) ? data.get(key) : null), setItem: (key, value) => data.set(key, String(value)) };
}

test("dismissals are remembered per note and survive a reload", () => {
  const storage = fakeStorage();

  rememberDismissed(storage, "n1", "project:p1");
  rememberDismissed(storage, "n1", "source:s1");
  rememberDismissed(storage, "n2", "project:p9");

  assert.deepEqual([...loadDismissed(storage, "n1")].sort(), ["project:p1", "source:s1"]);
  assert.deepEqual([...loadDismissed(storage, "n2")], ["project:p9"]);
  assert.deepEqual([...loadDismissed(storage, "n3")], []);
});

test("a storage that throws or holds garbage gives no dismissals and does not crash", () => {
  const broken = {
    getItem: () => {
      throw new Error("blocked");
    },
    setItem: () => {
      throw new Error("blocked");
    },
  };
  const garbage = { getItem: () => "{not json", setItem: () => {} };

  assert.deepEqual([...loadDismissed(broken, "n1")], []);
  assert.doesNotThrow(() => rememberDismissed(broken, "n1", "project:p1"));
  assert.deepEqual([...loadDismissed(garbage, "n1")], []);
});

test("when two inbox renders overlap, only the newest may write the page", () => {
  const renders = newestRender();

  const first = renders.start();
  const second = renders.start();

  assert.equal(renders.isCurrent(first), false);
  assert.equal(renders.isCurrent(second), true);
});

test("the inbox route names a tab, and a decisions route can name a project", () => {
  assert.deepEqual(parseInboxRoute("#/inbox"), { tab: "inbox", projectId: null });
  assert.deepEqual(parseInboxRoute("#/inbox/filed"), { tab: "filed", projectId: null });
  assert.deepEqual(parseInboxRoute("#/inbox/decisions"), { tab: "decisions", projectId: null });
  assert.deepEqual(parseInboxRoute("#/inbox/decisions/abc-123"), { tab: "decisions", projectId: "abc-123" });
  assert.deepEqual(parseInboxRoute("#/inbox/nonsense"), { tab: "inbox", projectId: null });
  assert.equal(parseInboxRoute("#/p/abc-123/overview"), null);
});

test("promoted items are found by the note they came from", () => {
  const items = [
    { id: "i1", kind: "hypothesis", content: "H1", promoted_from_note_id: "n1" },
    { id: "i2", kind: "idea", content: "Other", promoted_from_note_id: null },
    { id: "i3", kind: "memo", content: "M", promoted_from_note_id: "n2" },
  ];

  const map = promotedInto(items);

  assert.deepEqual(map.get("n1"), { id: "i1", kind: "hypothesis", content: "H1" });
  assert.equal(map.has("i2"), false);
  assert.equal(map.get("n2").kind, "memo");
});

test("decision rows show the rationale and the item each decision concerns", () => {
  const items = [
    { id: "h2", kind: "hypothesis", content: "H2: care burden", rationale: null, related_item_id: null, created_at: "2026-10-01T08:00:00Z" },
    { id: "d1", kind: "decision", content: "Dropped H2", rationale: "No effect in pilot", related_item_id: "h2", created_at: "2026-10-02T08:00:00Z" },
    { id: "d0", kind: "decision", content: "Chose survey", rationale: null, related_item_id: null, created_at: "2026-10-03T08:00:00Z" },
  ];

  assert.deepEqual(decisionRows(items), [
    { id: "d0", content: "Chose survey", rationale: "", concerns: null, createdAt: "2026-10-03T08:00:00Z" },
    { id: "d1", content: "Dropped H2", rationale: "No effect in pilot", concerns: "H2: care burden", createdAt: "2026-10-02T08:00:00Z" },
  ]);
});

test("the promote form starts from the note's own words, and a clip starts from its clipped text", () => {
  assert.deepEqual(promoteDefaults({ kind: "typed", body: "  Does remote work widen it?  ", quoted_text: null }), {
    content: "Does remote work widen it?",
    rationale: "",
  });
  assert.deepEqual(promoteDefaults({ kind: "clip", body: "", quoted_text: "Women's LFPR fell to 32%" }), {
    content: "Women's LFPR fell to 32%",
    rationale: "",
  });
});

import { canEditNote, editConflict, isOfflineFailure, planNoteEdit } from "../inboxLogic.js";

test("saving a note edit sends the trimmed text with the revision it was made from", () => {
  const note = { body: "old", revision: 4, status: "inbox" };
  assert.deepEqual(planNoteEdit(note, "  new words \n"), { action: "send", body: "new words", baseRevision: 4 });
});

test("an empty or unchanged edit is not sent", () => {
  const note = { body: "same", revision: 2, status: "inbox" };
  assert.deepEqual(planNoteEdit(note, "   "), { action: "invalid" });
  assert.deepEqual(planNoteEdit(note, "same "), { action: "unchanged" });
});

test("archived notes cannot be edited", () => {
  assert.equal(canEditNote({ status: "archived" }), false);
  assert.equal(canEditNote({ status: "inbox" }), true);
  assert.equal(canEditNote({ status: "filed" }), true);
});

test("only a request that never reached the server is queued offline", () => {
  assert.equal(isOfflineFailure(new Error("Can't reach the server.")), true);
  assert.equal(isOfflineFailure(Object.assign(new Error("x"), { status: 422 })), false);
  assert.equal(isOfflineFailure(Object.assign(new Error("x"), { status: 409 })), false);
});

test("a stale-revision refusal exposes the server's note; other errors do not", () => {
  const current = { id: "n1", body: "server", revision: 6 };
  const stale = Object.assign(new Error("x"), { status: 409, detail: { code: "note_conflict", current } });
  assert.deepEqual(editConflict(stale), { current });
  assert.equal(editConflict(Object.assign(new Error("x"), { status: 409, detail: "other" })), null);
  assert.equal(editConflict(Object.assign(new Error("x"), { status: 500 })), null);
  assert.equal(editConflict(new Error("offline")), null);
});

test("an outbox sync that sent or blocked anything asks for an Inbox rerender, a quiet one does not (X.94)", () => {
  assert.equal(outboxSyncChangedNotes({ sent: 1, blocked: 0, failed: 0 }), true);
  assert.equal(outboxSyncChangedNotes({ sent: 0, blocked: 1, failed: 0 }), true);
  assert.equal(outboxSyncChangedNotes({ sent: 0, blocked: 0, failed: 2 }), false);
  assert.equal(outboxSyncChangedNotes({ sent: 0, blocked: 0, failed: 0 }), false);
});

test("after a sync the Inbox is redrawn only when it is on screen and no form is open (X.94)", () => {
  const sent = { sent: 1, blocked: 0, failed: 0 };
  assert.equal(inboxRefreshAfterSync(sent, { inboxHidden: false, formOpen: false }), true);
  // The Inbox section is always in the page; a hidden one (another screen is showing) must not be redrawn.
  assert.equal(inboxRefreshAfterSync(sent, { inboxHidden: true, formOpen: false }), false);
  // Redrawing replaces the whole view, so an open edit or promote form would lose what the researcher typed.
  assert.equal(inboxRefreshAfterSync(sent, { inboxHidden: false, formOpen: true }), false);
  assert.equal(inboxRefreshAfterSync({ sent: 0, blocked: 0, failed: 1 }, { inboxHidden: false, formOpen: false }), false);
});

test("app.js hands the sync result to the Inbox instead of guarding on the always-present section (X.94)", () => {
  const appJs = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "..", "..", "app.js"), "utf8");
  assert.match(appJs, /await refreshInboxAfterSync\(result\)/);
  assert.doesNotMatch(appJs, /&& \$\("#inbox-view"\)\)/);
});
