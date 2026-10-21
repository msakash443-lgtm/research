// Run with: node --test backend/app/web/js/test   (no packages; pytest does not run these)
import test from "node:test";
import assert from "node:assert/strict";
import { afterDecision, authorsText, confidenceText, countsText, fetchEntireQueue, keyAction, runBulk, safeLink, splitAtQuote, typedReason } from "../steps/screenLogic.js";

test("a DOI becomes an https://doi.org link with slashes kept and unsafe characters escaped", () => {
  assert.equal(safeLink("10.1000/abc.1"), "https://doi.org/10.1000/abc.1");
  assert.equal(safeLink('10.1/a b"<x>'), "https://doi.org/10.1/a%20b%22%3Cx%3E");
  assert.equal(safeLink("javascript:alert(1)").startsWith("https://doi.org/"), true);
  assert.equal(safeLink(null), null);
  assert.equal(safeLink(""), null);
});

test("authors are listed, shortened after six, and a missing list says so", () => {
  assert.equal(authorsText(["A", "B"]), "A, B");
  assert.equal(authorsText([{ name: "A" }, { name: "" }, {}]), "A");
  assert.equal(authorsText(["1", "2", "3", "4", "5", "6", "7", "8"]), "1, 2, 3, 4, 5, 6 and 2 more");
  assert.equal(authorsText(null), "Authors not recorded");
  assert.equal(authorsText([]), "Authors not recorded");
});

test("the counts line shows zeros for missing counts and the AI figure only when present", () => {
  assert.equal(countsText({}, 3), "To screen: 3 · Included: 0 · Excluded: 0 · Maybe: 0");
  assert.match(countsText({ include: 1, ai_auto_excluded: 2 }, 0), /Included: 1 .*AI-excluded \(counted\): 2$/);
});

test("a decision raises its own count, lowers unscreened, floors at zero and leaves the input alone", () => {
  const before = { include: 1, unscreened: 2 };
  assert.deepEqual(afterDecision(before, "exclude"), { include: 1, unscreened: 1, exclude: 1 });
  assert.deepEqual(before, { include: 1, unscreened: 2 });
  assert.equal(afterDecision({ unscreened: 0 }, "maybe").unscreened, 0);
});

test("keys map to actions in either case", () => {
  assert.equal(keyAction({ key: "i" }), "include");
  assert.equal(keyAction({ key: "E" }), "exclude");
  assert.equal(keyAction({ key: "?" }), "help");
  assert.equal(keyAction({ key: "x" }), null);
});

test("no key fires with a modifier, in a form field, or while a dialog is open", () => {
  assert.equal(keyAction({ key: "i", ctrlKey: true }), null);
  assert.equal(keyAction({ key: "i", metaKey: true }), null);
  assert.equal(keyAction({ key: "i", altKey: true }), null);
  for (const tag of ["INPUT", "TEXTAREA", "SELECT"]) assert.equal(keyAction({ key: "i", targetTag: tag }), null);
  assert.equal(keyAction({ key: "i", targetTag: "BODY" }), "include");
  assert.equal(keyAction({ key: "i", dialogOpen: true }), null);
});

test("a bulk run sends every record in order and reports none failed", async () => {
  const sent = [];
  const result = await runBulk(["a", "b", "c"], async (id) => sent.push(id));
  assert.deepEqual(sent, ["a", "b", "c"]);
  assert.deepEqual(result, { done: ["a", "b", "c"], failure: null });
});

test("a bulk run stops at the first failure and never sends the records after it", async () => {
  const sent = [];
  const result = await runBulk(["a", "b", "c"], async (id) => {
    sent.push(id);
    if (id === "b") throw new Error("Screening is locked");
  });
  assert.deepEqual(sent, ["a", "b"]);
  assert.deepEqual(result, { done: ["a"], failure: "Screening is locked" });
});

test("a bulk run with no records sends nothing", async () => {
  assert.deepEqual(await runBulk([], async () => assert.fail("must not send")), { done: [], failure: null });
});

test("a reason number typed in full picks at once, a lone digit that could extend waits, and an impossible number picks nothing", () => {
  assert.deepEqual(typedReason("1", 9), { index: 0 });
  assert.deepEqual(typedReason("1", 12), { wait: true });
  assert.deepEqual(typedReason("10", 12), { index: 9 });
  assert.deepEqual(typedReason("13", 12), {});
  assert.deepEqual(typedReason("0", 12), {});
});

test("every page of the queue is loaded in order, with no record lost or repeated, whatever the total", async () => {
  for (const total of [0, 1, 199, 200, 201, 400, 453]) {
    const all = Array.from({ length: total }, (_, i) => ({ source_id: `s${i}` }));
    const calls = [];
    const result = await fetchEntireQueue(async (offset) => {
      calls.push(offset);
      return { items: all.slice(offset, offset + 200), pending: total };
    });
    assert.deepEqual(result.items, all, `total ${total}`);
    assert.equal(result.pending, total);
    assert.equal(new Set(calls).size, calls.length, `no page requested twice (total ${total})`);
  }
});

test("a record that appears on two pages is kept once, and the next page is asked for by where the server's list is", async () => {
  // The server's list shifts by one between the two requests: s200 is served on both pages.
  const pages = [
    Array.from({ length: 200 }, (_, i) => ({ source_id: `s${i}` })),
    Array.from({ length: 200 }, (_, i) => ({ source_id: `s${i + 200}` })),
    [{ source_id: "s400" }],
  ];
  const calls = [];
  const result = await fetchEntireQueue(async (offset) => {
    calls.push(offset);
    const shifted = offset === 200 ? [{ source_id: "s199" }, ...pages[1].slice(0, 199)] : pages[offset / 200] || [];
    return { items: shifted, pending: 401 };
  });
  const ids = result.items.map((i) => i.source_id);
  assert.equal(new Set(ids).size, ids.length, "no record twice");
  assert.equal(ids.includes("s199"), true);
  assert.deepEqual(calls, [0, 200, 400]);
});

test("the AI's quote is found in the record ignoring case and line breaks, and split around the match", () => {
  const text = "We surveyed 200 nurses.\nWorkload was linked to burnout in every ward.";
  assert.deepEqual(splitAtQuote(text, "workload was LINKED to burnout"),
    ["We surveyed 200 nurses.\n", "Workload was linked to burnout", " in every ward."]);
  assert.deepEqual(splitAtQuote(text, "nurses. workload")[1], "nurses.\nWorkload");
});

test("a quote that is not in the text, or is empty, marks nothing", () => {
  assert.equal(splitAtQuote("Workload was linked to burnout", "Workload caused burnout"), null);
  assert.equal(splitAtQuote("Workload", "   "), null);
  assert.equal(splitAtQuote("", "x"), null);
});

test("quote characters that mean something in a pattern are matched as plain text", () => {
  assert.deepEqual(splitAtQuote("Effect (n=20) was small. Done", "(n=20) was small."), ["Effect ", "(n=20) was small.", " Done"]);
  assert.equal(splitAtQuote("abc", ".*"), null);
});

test("the confidence line names the level and the percentage, and is empty without a level", () => {
  assert.equal(confidenceText("high", 0.9), "High confidence (90%)");
  assert.equal(confidenceText("low", undefined), "Low confidence");
  assert.equal(confidenceText(undefined, 0.9), "");
});
