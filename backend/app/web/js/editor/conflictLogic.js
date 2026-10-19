// Document version-conflict resolution (X.31.16). The server refuses a save with a 409 `version_conflict` (and the
// current server version) when the editor's `base_number` is behind the server's latest (`routers/documents.py`'s
// `_conflict`): someone else saved first, or this browser saved the same section from another tab. The compare view
// in `steps/write.js` shows the researcher's local text next to the server's, then asks keep-mine / keep-theirs /
// keep-both.
//
// This module is the first real caller of `offlineOutbox.js`'s `resolveConflict` (built for X.31.5's note-capture
// outbox but never wired to anything that returns a 409 there): a document conflict reuses the same mine/theirs/both
// decision instead of a second implementation. `resolveConflict` works on a `body` string, so this bridges a
// ProseMirror `content_json` doc to plain text for the decision and, for "both", back to a doc.
import * as PM from "../../vendor/prosemirror/prosemirror.bundle.js";
import { schema } from "./schema.js";
import { resolveConflict as resolveOutboxConflict } from "../offlineOutbox.js";

// Plain text of a saved content_json, one block per line, for the compare view and the "both" merge. Unreadable
// content (should not happen for anything the server has stored) reads as empty rather than throwing.
export function textOf(contentJson) {
  try {
    const doc = PM.model.Node.fromJSON(schema, contentJson);
    return doc.textBetween(0, doc.content.size, "\n", "\n");
  } catch {
    return "";
  }
}

// A minimal paragraph-per-line doc built from plain text, used for the "both" merge's combined content. It carries
// no provenance marks: the merge is a starting point the researcher keeps editing, not a finished, attributed save.
function docFromLines(text) {
  const lines = text.split("\n").filter((line) => line.length > 0);
  const paragraphs = (lines.length ? lines : [""]).map((line) => ({
    type: "paragraph", content: line ? [{ type: "text", text: line }] : [],
  }));
  return { type: "doc", content: paragraphs };
}

// Builds what the compare view should do for one of the three choices. Returns either
// `{ action: "retry", content_json }` (editor shows `content_json` and saves it against the server's new base) or
// `{ action: "drop", content_json }` (editor shows the server's text; nothing new is saved).
export function planConflictResolution(mineContentJson, theirsContentJson, choice) {
  const mine = { body: textOf(mineContentJson) };
  const theirs = { body: textOf(theirsContentJson) };
  const resolved = resolveOutboxConflict(mine, theirs, choice);
  if (resolved.action === "drop") return { action: "drop", content_json: theirsContentJson };
  if (choice === "both") return { action: "retry", content_json: docFromLines(resolved.payload.body) };
  return { action: "retry", content_json: mineContentJson };
}
