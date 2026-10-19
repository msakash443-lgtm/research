// Run with: node --test backend/app/web/js/test/editor.test.mjs
// The provenance rules the editor must keep so the server's ledger accepts a save (X.31.4, mirrors app/ledger.py).
import { test } from "node:test";
import assert from "node:assert/strict";
import * as PM from "../../vendor/prosemirror/prosemirror.bundle.js";
import { schema } from "../editor/schema.js";
import { AI_INSERT_META, PROV_TYPE, markPasted, provenancePlugin, resolvePaste, summarize, spansOf } from "../editor/provenance.js";

const ME = "user-me";
const OTHER = "user-other";

function sidMaker(prefix) {
  let n = 0;
  return () => `${prefix}-${++n}`;
}

function stateWith(doc, newSid = sidMaker("new")) {
  return PM.state.EditorState.create({ schema, doc, plugins: [provenancePlugin({ actor: ME, newSidFn: newSid })] });
}

function emptyDoc() {
  return schema.node("doc", null, [schema.node("paragraph")]);
}

// A text run already written by `by` (what a saved version holds).
function written(text, sid, by, origin = "typed", paste = null) {
  return schema.text(text, [PROV_TYPE.create({ sid, origin, by, sug: null, note: null, paste, src: null })]);
}

function docWith(...inline) {
  return schema.node("doc", null, [schema.node("paragraph", null, inline)]);
}

test("typed text becomes one span owned by the author", () => {
  let state = stateWith(emptyDoc(), sidMaker("t"));
  state = state.apply(state.tr.insertText("Care work is unpaid.", 1));

  const spans = spansOf(state.doc);
  assert.equal(spans.size, 1);
  const [span] = spans.values();
  assert.equal(span.text, "Care work is unpaid.");
  assert.equal(span.origin, "typed");
  assert.equal(span.by, ME);
});

test("typing at the end of your own span keeps the same span id", () => {
  let state = stateWith(emptyDoc(), sidMaker("t"));
  state = state.apply(state.tr.insertText("First. ", 1));
  const sidBefore = [...spansOf(state.doc).keys()][0];
  state = state.apply(state.tr.insertText("Second.", state.doc.content.size - 1));

  const spans = spansOf(state.doc);
  assert.equal(spans.size, 1);
  assert.equal(spans.get(sidBefore).text, "First. Second.");
});

test("pasted text is its own span, unconfirmed, with a new id", () => {
  let state = stateWith(emptyDoc(), sidMaker("t"));
  state = state.apply(state.tr.insertText("Mine.", 1));
  const slice = new PM.model.Slice(PM.model.Fragment.from(schema.text("A long quoted sentence from a paper.")), 0, 0);
  const pasted = markPasted(slice, ME, "paste-1");
  state = state.apply(state.tr.replace(state.doc.content.size - 1, state.doc.content.size - 1, pasted));

  const pastedSpan = spansOf(state.doc).get("paste-1");
  assert.equal(pastedSpan.origin, "pasted");
  assert.equal(pastedSpan.paste, "unconfirmed");
  assert.equal(pastedSpan.by, ME);
  assert.equal(pastedSpan.text, "A long quoted sentence from a paper.");
});

test("typing inside a pasted span does not join it: the new text is typed by the author", () => {
  const pasted = written("Quoted words here", "paste-1", ME, "pasted", "unconfirmed");
  let state = stateWith(docWith(pasted), sidMaker("typed"));
  // "Quoted " is 7 characters, so position 8 is inside the pasted run (paragraph starts at 1).
  state = state.apply(state.tr.insertText("NEW ", 8));

  const spans = spansOf(state.doc);
  const pastedText = [...spans.values()].filter((s) => s.origin === "pasted").map((s) => s.text);
  assert.deepEqual(pastedText, ["Quoted words here"]);
  assert.equal(spans.get("paste-1").text, "Quoted words here");
  const typed = [...spans.values()].find((s) => s.origin === "typed");
  assert.equal(typed.text, "NEW ");
  assert.equal(typed.by, ME);
});

test("another author's span is never changed by a neighbouring edit", () => {
  const theirs = written("Their sentence. ", "their-1", OTHER);
  const mine = written("Mine. ", "mine-1", ME);
  let state = stateWith(docWith(theirs, mine), sidMaker("typed"));
  state = state.apply(state.tr.insertText("More. ", state.doc.content.size - 1));

  const spans = spansOf(state.doc);
  assert.equal(spans.get("their-1").by, OTHER);
  assert.equal(spans.get("their-1").text, "Their sentence. ");
  assert.equal(spans.get("mine-1").text, "Mine. More. ");
});

test("resolving a paste changes only its paste status", () => {
  const pasted = written("Pasted passage.", "paste-1", ME, "pasted", "unconfirmed");
  const mine = written(" Mine.", "mine-1", ME);
  let state = stateWith(docWith(pasted, mine));

  state = state.apply(resolvePaste(state, "paste-1", "quotation"));

  const spans = spansOf(state.doc);
  assert.equal(spans.get("paste-1").paste, "quotation");
  assert.equal(spans.get("paste-1").origin, "pasted");
  assert.equal(spans.get("mine-1").paste, null);
});

test("resolving a paste does not get its own text re-labelled as typed", () => {
  const pasted = written("Pasted passage.", "paste-1", ME, "pasted", "unconfirmed");
  let state = stateWith(docWith(pasted));
  state = state.apply(resolvePaste(state, "paste-1", "own"));

  assert.equal(spansOf(state.doc).get("paste-1").origin, "pasted");
});

test("summarize counts characters by origin and by paste status", () => {
  const doc = docWith(
    written("Typed. ", "t", ME),
    written("Quoted words.", "p1", ME, "pasted", "quotation"),
    written(" Unsure.", "p2", ME, "pasted", "unconfirmed"),
  );

  assert.deepEqual(summarize(doc), {
    characters: 7 + 13 + 8,
    byOrigin: { typed: 7, pasted: 21 },
    unconfirmed: 1,
    quotations: 1,
  });
});

test("the space typed at the edge of a pasted span is the author's, not the paste's", () => {
  const pasted = written("Quoted words here", "paste-1", ME, "pasted", "unconfirmed");
  let state = stateWith(docWith(pasted), sidMaker("typed"));
  state = state.apply(state.tr.insertText("NEW ", 8));

  const spans = spansOf(state.doc);
  assert.equal(spans.get("paste-1").text, "Quoted words here");
  assert.equal(spans.get("paste-1").origin, "pasted");
  assert.equal([...spans.values()].find((s) => s.origin === "typed").text, "NEW ");
});

// An AI insert from the side panel's Insert button (X.31.11.2): the new text is labelled ai_edited and tied to the
// accepted suggestion, and the author's own text next to it stays theirs.
test("text inserted from an accepted AI suggestion is labelled ai_edited with its suggestion id", () => {
  let state = stateWith(docWith(written("Old wording. ", "a", ME)), sidMaker("ai"));
  const insertAt = state.doc.content.size - 1;
  state = state.apply(state.tr.insertText("Clearer wording.", insertAt).setMeta(AI_INSERT_META, { suggestionId: "sug-1" }));

  const spans = [...spansOf(state.doc).values()];
  const ai = spans.find((span) => span.text === "Clearer wording.");
  assert.equal(ai.origin, "ai_edited");
  assert.equal(ai.sug, "sug-1");
  assert.equal(ai.by, ME);
  const own = spans.find((span) => span.text === "Old wording. ");
  assert.equal(own.origin, "typed");
});

// A whole AI-drafted passage inserted at the cursor (X.46.8), not an edit of existing wording: it passes
// origin "ai_suggested" explicitly, while the clearer-sentence call site above (no origin passed) still defaults
// to "ai_edited".
test("a draft inserted at the cursor is labelled ai_suggested when origin is passed", () => {
  let state = stateWith(docWith(written("Old wording. ", "a", ME)), sidMaker("draft"));
  const insertAt = state.doc.content.size - 1;
  state = state.apply(state.tr.insertText("Drafted paragraph.", insertAt).setMeta(AI_INSERT_META, { suggestionId: "sug-9", origin: "ai_suggested" }));

  const spans = [...spansOf(state.doc).values()];
  const draft = spans.find((span) => span.text === "Drafted paragraph.");
  assert.equal(draft.origin, "ai_suggested");
  assert.equal(draft.sug, "sug-9");
  assert.equal(draft.by, ME);
});

test("an ordinary insert is still typed by the author, even right after an AI insert", () => {
  let state = stateWith(docWith(written("Start. ", "a", ME)), sidMaker("t"));
  state = state.apply(state.tr.insertText("AI.", state.doc.content.size - 1).setMeta(AI_INSERT_META, { suggestionId: "sug-2" }));
  state = state.apply(state.tr.insertText(" Mine.", state.doc.content.size - 1));
  const typed = [...spansOf(state.doc).values()].find((span) => span.text.includes("Mine."));
  assert.equal(typed.origin, "typed");
});
