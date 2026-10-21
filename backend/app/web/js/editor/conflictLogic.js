// Document version-conflict resolution (X.31.16). The server refuses a save with a 409 `version_conflict` (and the
// current server version) when the editor's `base_number` is behind the server's latest (`routers/documents.py`'s
// `_conflict`): someone else saved first, or this browser saved the same section from another tab. The compare view
// in `steps/write.js` shows the researcher's local text next to the server's, then asks keep-mine / keep-theirs /
// keep-both.
//
// This module is the first real caller of `offlineOutbox.js`'s `resolveConflict` (built for X.31.5's note-capture
// outbox but never wired to anything that returns a 409 there): a document conflict reuses the same mine/theirs/both
// decision instead of a second implementation. `resolveConflict` works on a `body` string, so this bridges a
// ProseMirror `content_json` doc to plain text for the decision; "both" then merges the two docs themselves
// (`mergeBoth`) so every run keeps its provenance mark.
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

// Every span id (`prov.attrs.sid`) used by a text run in a doc.
function spanIds(node, ids = new Set()) {
  const sid = node.type === "text" ? (node.marks || []).find((mark) => mark.type === "prov")?.attrs?.sid : null;
  if (sid) ids.add(sid);
  for (const child of node.content || []) spanIds(child, ids);
  return ids;
}

// `node` without the text runs whose span is already in the saved version, or null when no text of its own is left.
// Blocks keep their other inline nodes (citations, numbers) only when some local text survives with them.
function localOnly(node, savedIds) {
  if (node.type === "text") {
    const sid = (node.marks || []).find((mark) => mark.type === "prov")?.attrs?.sid;
    return sid && savedIds.has(sid) ? null : node;
  }
  if (!node.content) return null;
  const kept = node.content.map((child) => localOnly(child, savedIds)).filter(Boolean);
  const hasText = (child) => child.type === "text" || (child.content || []).some(hasText);
  return kept.some(hasText) ? { ...node, content: kept } : null;
}

// "Keep both" as a saveable doc: the saved version exactly as it is, then the blocks holding text only this browser
// wrote. Every run keeps the `prov` mark it already has (the server refuses unmarked text, `ledger.validate_schema`),
// so nothing is relabelled as the researcher's own, and runs whose span the saved version already holds are left out
// so no span id appears twice. Relabelled merge text would misstate the writing record.
function mergeBoth(mineContentJson, theirsContentJson) {
  const savedIds = spanIds(theirsContentJson);
  const mineOnly = (mineContentJson.content || []).map((block) => localOnly(block, savedIds)).filter(Boolean);
  return { ...theirsContentJson, content: [...(theirsContentJson.content || []), ...mineOnly] };
}

// Builds what the compare view should do for one of the three choices. Returns either
// `{ action: "retry", content_json }` (editor shows `content_json` and saves it against the server's new base) or
// `{ action: "drop", content_json }` (editor shows the server's text; nothing new is saved).
export function planConflictResolution(mineContentJson, theirsContentJson, choice) {
  const mine = { body: textOf(mineContentJson) };
  const theirs = { body: textOf(theirsContentJson) };
  const resolved = resolveOutboxConflict(mine, theirs, choice);
  if (resolved.action === "drop") return { action: "drop", content_json: theirsContentJson };
  if (choice === "both") return { action: "retry", content_json: mergeBoth(mineContentJson, theirsContentJson) };
  return { action: "retry", content_json: mineContentJson };
}
