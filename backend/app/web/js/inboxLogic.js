// Pure logic for the inbox screen (X.31.2.1): what to show and what a click would send. No DOM, no fetch,
// so it can be tested with `node --test backend/app/web/js/test`. The server still checks every rule these
// functions anticipate (write role, filed-only promotion, same-project related item).

const WRITE_ROLES = new Set(["owner", "co_author"]);

// The projects a note may be filed to: the caller owns them or is an owner or co-author.
export function writableProjects(projects, membersByProject, userId) {
  return projects
    .filter((project) => {
      if (project.owner_id === userId) return true;
      return (membersByProject[project.id] || []).some((member) => member.user_id === userId && WRITE_ROLES.has(member.role));
    })
    .map((project) => ({ id: project.id, title: project.title }));
}

export const INBOX_TABS = ["inbox", "filed", "archived", "decisions"];

// Suggestion chips for one note. Each chip files the note to the project it names: a project, its stage, or a
// source's project. The server checks the caller can write there. A dismissed chip is hidden.
export function suggestionChips(suggestions, dismissed = new Set()) {
  const chips = [];
  for (const item of suggestions) {
    const key = `${item.kind}:${item.target_id}`;
    if (dismissed.has(key)) continue;
    if (item.kind === "project") {
      chips.push({ key, text: item.label, actionable: true, projectId: item.target_id });
    } else if (item.kind === "stage") {
      chips.push({ key, text: `Stage: ${item.label}`, actionable: true, projectId: item.project_id });
    } else if (item.kind === "source") {
      chips.push({ key, text: `Source: ${item.label}`, actionable: true, projectId: item.project_id });
    }
  }
  return chips;
}

// Dismissed chips are kept per browser in one storage entry: {noteId: [chip keys]}. The storage object is
// localStorage or anything with the same getItem/setItem. If it is blocked or holds bad data, nothing is
// remembered and the chips simply show again: a convenience, not a record.
const DISMISS_STORAGE_KEY = "ra.inbox.dismissed.v1";
const MAX_DISMISSED_PER_NOTE = 20;
const MAX_NOTES_WITH_DISMISSALS = 200;

function readDismissals(storage) {
  const parsed = JSON.parse(storage.getItem(DISMISS_STORAGE_KEY) || "{}");
  return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
}

export function loadDismissed(storage, noteId) {
  try {
    const keys = readDismissals(storage)[noteId];
    return new Set(Array.isArray(keys) ? keys : []);
  } catch {
    return new Set();
  }
}

export function rememberDismissed(storage, noteId, key) {
  try {
    const map = readDismissals(storage);
    const keys = new Set(Array.isArray(map[noteId]) ? map[noteId] : []);
    keys.add(key);
    // Re-inserting moves this note to the end, so the oldest notes are the first to go.
    delete map[noteId];
    map[noteId] = [...keys].slice(-MAX_DISMISSED_PER_NOTE);
    const noteIds = Object.keys(map);
    for (const id of noteIds.slice(0, Math.max(0, noteIds.length - MAX_NOTES_WITH_DISMISSALS))) delete map[id];
    storage.setItem(DISMISS_STORAGE_KEY, JSON.stringify(map));
  } catch {
    // Storage is blocked or full: the chip stays hidden for this page visit only.
  }
}

// Renders of the inbox can overlap (a slow response for one tab while another is opened). Each render takes a
// token; only the newest may write the page, so an old render cannot overwrite the page the user is on.
export function newestRender() {
  let current = 0;
  return {
    start() {
      current += 1;
      return current;
    },
    isCurrent(token) {
      return token === current;
    },
  };
}

// The inbox route: `#/inbox`, `#/inbox/{tab}`, or `#/inbox/decisions/{projectId}`. An unknown tab is the inbox.
// The project id is only read for the decisions tab. Returns null when the hash is not an inbox route.
export function parseInboxRoute(hash) {
  const match = hash.match(/^#\/inbox(?:\/([a-z]+)(?:\/([^/]+))?)?$/);
  if (!match) return null;
  const tab = INBOX_TABS.includes(match[1]) ? match[1] : "inbox";
  return { tab, projectId: tab === "decisions" && match[2] ? match[2] : null };
}

// Which context item (if any) was promoted from each note, keyed by note id.
export function promotedInto(items) {
  const byNote = new Map();
  for (const item of items) {
    if (item.promoted_from_note_id) {
      byNote.set(item.promoted_from_note_id, { id: item.id, kind: item.kind, content: item.content });
    }
  }
  return byNote;
}

// The decision log: decision items, newest first, each with the item it concerns named.
export function decisionRows(items) {
  const byId = new Map(items.map((item) => [item.id, item]));
  return items
    .filter((item) => item.kind === "decision")
    .sort((a, b) => (a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : 0))
    .map((item) => ({
      id: item.id,
      content: item.content,
      rationale: item.rationale || "",
      concerns: item.related_item_id && byId.has(item.related_item_id) ? byId.get(item.related_item_id).content : null,
      createdAt: item.created_at,
    }));
}

// The promote form starts from the researcher's own words. For a clip that is the clipped text, which the
// researcher then rewrites in their own words.
export function promoteDefaults(note) {
  const text = note.kind === "clip" ? note.quoted_text || "" : note.body || "";
  return { content: text.trim(), rationale: "" };
}

// Editing a saved note's body (X.31.25). What the Save click should do, decided before any request:
// "invalid" (empty after trimming: the server refuses it too), "unchanged" (nothing to save), or "send" with the
// trimmed text and the revision the edit is made from (the server's `base_revision` check).
export function planNoteEdit(note, text) {
  const body = (text || "").trim();
  if (!body) return { action: "invalid" };
  if (body === (note.body || "").trim()) return { action: "unchanged" };
  return { action: "send", body, baseRevision: note.revision };
}

// Archived notes are read-only in the inbox, as they are for filing and promoting.
export function canEditNote(note) {
  return note.status !== "archived";
}

// `request()` only sets `status` when the server answered, so an error without one means the request never got
// there (offline, server down): the edit is queued in the offline outbox instead of being lost.
export function isOfflineFailure(error) {
  return error?.status === undefined;
}

// A 409 from PATCH /api/notes/{id} carries the server's current note when the edit's base revision is stale.
export function editConflict(error) {
  const current = error?.status === 409 && error.detail?.code === "note_conflict" ? error.detail.current : null;
  return current ? { current } : null;
}

// An outbox flush that sent or blocked an item changed what the Inbox shows (the note's saved text or its conflict
// choice), so the Inbox must be redrawn; a flush that only failed left everything as it was (X.94).
export function outboxSyncChangedNotes(result) {
  return (result?.sent || 0) + (result?.blocked || 0) > 0;
}

// Redraw only an Inbox that is on screen (its section is always in the page, just hidden) and has no open edit or
// promote form: a redraw replaces the whole view and would drop what the researcher was typing (X.94).
export function inboxRefreshAfterSync(result, { inboxHidden, formOpen }) {
  return outboxSyncChangedNotes(result) && !inboxHidden && !formOpen;
}
