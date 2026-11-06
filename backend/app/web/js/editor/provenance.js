// Authorship marks for the writing editor (X.31.4). The rules mirror app/ledger.py, so a save the editor makes passes
// the server's checks: every text run has one `prov` mark, a span is the runs that share a `sid`, and a span's origin,
// author and suggestion never change once written. Typed text is the author's; pasted text is its own span until the
// author says whether it is their own words or a quotation.
import * as PM from "../../vendor/prosemirror/prosemirror.bundle.js";
import { schema } from "./schema.js";

export const PROV_TYPE = schema.marks.prov;
// Transaction meta: the editor itself is loading, restoring or resolving text, so the plugin must not re-mark it.
export const PROVENANCE_META = "provenance";
// Transaction meta for text the researcher inserted from an accepted AI suggestion (the side panel's Insert button).
// Its value is { suggestionId }. Only that button sets it: the plugin labels the inserted text `ai_edited`.
export const AI_INSERT_META = "ai-insert";

export function newSid() {
  return globalThis.crypto.randomUUID();
}

function isTyped(attrs, actor) {
  return Boolean(attrs) && attrs.origin === "typed" && attrs.by === actor;
}

// Every text run in document order, with its prov attrs (null when a run has none).
function textRuns(doc) {
  const runs = [];
  doc.descendants((node, pos) => {
    if (node.isText) runs.push({ from: pos, to: pos + node.nodeSize, attrs: provAttrs(node) });
  });
  return runs;
}

export function provAttrs(node) {
  const mark = node.marks.find((m) => m.type === PROV_TYPE);
  return mark ? mark.attrs : null;
}

// Spans by sid, in document order: text, origin, author, paste status and source.
export function spansOf(doc) {
  const spans = new Map();
  for (const run of textRuns(doc)) {
    if (!run.attrs) continue;
    const { sid } = run.attrs;
    if (!spans.has(sid)) spans.set(sid, { ...run.attrs, text: "" });
    spans.get(sid).text += doc.textBetween(run.from, run.to);
  }
  return spans;
}

// Pasted text becomes one unconfirmed span with a new sid, whatever provenance the copied text carried.
export function markPasted(slice, actor, sid = newSid()) {
  const mark = (node) => {
    if (node.isText) {
      const kept = node.marks.filter((m) => m.type !== PROV_TYPE);
      const attrs = { sid, origin: "pasted", by: actor, sug: null, note: null, paste: "unconfirmed", src: null };
      return node.mark([...kept, PROV_TYPE.create(attrs)]);
    }
    if (node.isLeaf) return node;
    return node.copy(mapFragment(node.content));
  };
  const mapFragment = (fragment) => {
    const children = [];
    fragment.forEach((child) => children.push(mark(child)));
    return PM.model.Fragment.from(children);
  };
  return new PM.model.Slice(mapFragment(slice.content), slice.openStart, slice.openEnd);
}

// Where the transactions in this batch inserted text, in the coordinates of the new document. Read from the steps,
// not from a diff: a diff cannot tell which of two identical spaces was typed, so it would misattribute the edge.
export function insertedRanges(transactions, doc) {
  const maps = [];
  const inserted = [];
  for (const tr of transactions) {
    tr.steps.forEach((step, i) => {
      const isReplace = step.slice && step.from !== undefined && step.to !== undefined && !("gapFrom" in step);
      if (isReplace && step.slice.size > 0) inserted.push({ from: step.from, to: step.from + step.slice.size, index: maps.length + i });
    });
    maps.push(...tr.mapping.maps);
  }
  return inserted
    .map((range) => {
      const later = new PM.transform.Mapping(maps.slice(range.index + 1));
      return [later.map(range.from, 1), later.map(range.to, -1)];
    })
    .filter(([from, to]) => from < to && to <= doc.content.size)
    .sort((a, b) => a[0] - b[0]);
}

function overlaps(from, to, ranges) {
  return ranges
    .map(([a, b]) => [Math.max(a, from), Math.min(b, to)])
    .filter(([a, b]) => a < b);
}

// Typed text gets the author's span: it continues the previous run when that is also the author's typing, otherwise it
// starts a new span. Text that came from another span (the edge of a pasted or someone else's run) is re-marked only
// where it was inserted, so typing next to or inside it never changes that span. Text with no provenance is marked.
export function assignTypedProvenance(newState, oldState, actor, inserted, newSidFn = newSid) {
  const oldSids = new Set(spansOf(oldState.doc).keys());
  const changes = [];
  newState.doc.descendants((block, blockPos) => {
    if (!block.isTextblock) return;
    let previous = null;
    block.forEach((child, offset) => {
      const from = blockPos + 1 + offset;
      const to = from + child.nodeSize;
      if (!child.isText) {
        previous = null;
        return;
      }
      const attrs = provAttrs(child);
      const inherited = attrs !== null && oldSids.has(attrs.sid) && !isTyped(attrs, actor);
      let pieces = [];
      if (attrs === null) pieces = [[from, to]];
      else if (inherited) pieces = overlaps(from, to, inserted);
      if (pieces.length === 0) {
        previous = attrs;
        return;
      }
      let cursor = from;
      for (const [a, b] of pieces) {
        if (a > cursor) previous = attrs;
        const continues = previous !== null && isTyped(previous, actor);
        const next = { sid: continues ? previous.sid : newSidFn(), origin: "typed", by: actor, sug: null, note: null, paste: null, src: null };
        changes.push({ from: a, to: b, attrs: next });
        previous = next;
        cursor = b;
      }
      if (cursor < to) previous = attrs;
    });
  });
  if (changes.length === 0) return null;

  const tr = newState.tr;
  for (const change of changes) {
    tr.removeMark(change.from, change.to, PROV_TYPE);
    tr.addMark(change.from, change.to, PROV_TYPE.create(change.attrs));
  }
  return tr;
}

export function provenancePlugin({ actor, newSidFn = newSid }) {
  return new PM.state.Plugin({
    props: {
      transformPasted: (slice) => markPasted(slice, actor, newSidFn()),
    },
    appendTransaction(transactions, oldState, newState) {
      if (!transactions.some((tr) => tr.docChanged)) return null;
      const aiInsert = transactions.map((tr) => tr.getMeta(AI_INSERT_META)).find(Boolean);
      if (aiInsert) return markAiInserted(newState, insertedRanges(transactions, newState.doc), { actor, suggestionId: aiInsert.suggestionId, origin: aiInsert.origin, newSidFn });
      if (transactions.some((tr) => tr.getMeta(PROVENANCE_META))) return null;
      const inserted = insertedRanges(transactions, newState.doc);
      return assignTypedProvenance(newState, oldState, actor, inserted, newSidFn);
    },
  });
}

// Labels inserted text as AI wording the researcher chose: origin ai_edited by default, tied to the accepted
// suggestion. A whole AI-drafted passage inserted at the cursor (not replacing a selection) passes origin
// "ai_suggested" instead (X.46.8), the distinct origin `app/ledger.py`'s AI_ORIGINS already defines for that case.
// The server checks the tie (ai_text_without_accept, ai_text_mismatch) the same way for both origins.
export function markAiInserted(newState, inserted, { actor, suggestionId, origin = "ai_edited", newSidFn = newSid }) {
  if (inserted.length === 0) return null;
  const tr = newState.tr;
  for (const [from, to] of inserted) {
    tr.removeMark(from, to, PROV_TYPE);
    tr.addMark(from, to, PROV_TYPE.create({ sid: newSidFn(), origin, by: actor, sug: suggestionId, note: null, paste: null, src: null }));
  }
  return tr;
}

// Sets the paste status of a pasted span ("unconfirmed", "own" or "quotation"). Origin, author and id stay the same.
export function resolvePaste(state, sid, paste) {
  const tr = state.tr;
  for (const run of textRuns(state.doc)) {
    if (!run.attrs || run.attrs.sid !== sid || run.attrs.origin !== "pasted") continue;
    tr.removeMark(run.from, run.to, PROV_TYPE);
    tr.addMark(run.from, run.to, PROV_TYPE.create({ ...run.attrs, paste }));
  }
  tr.setMeta(PROVENANCE_META, "resolve");
  return tr;
}

// Characters by origin, and how many pasted spans are unconfirmed or quotations (counted in spans).
export function summarize(doc) {
  const summary = { characters: 0, byOrigin: {}, unconfirmed: 0, quotations: 0 };
  for (const span of spansOf(doc).values()) {
    const length = span.text.length;
    summary.characters += length;
    summary.byOrigin[span.origin] = (summary.byOrigin[span.origin] || 0) + length;
    if (span.origin === "pasted" && span.paste === "unconfirmed") summary.unconfirmed += 1;
    if (span.paste === "quotation") summary.quotations += 1;
  }
  return summary;
}
