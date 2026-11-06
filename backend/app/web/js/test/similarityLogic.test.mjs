// Run with: node --test backend/app/web/js/test   (no packages; pytest does not run these)
import test from "node:test";
import assert from "node:assert/strict";
import { flagsFor, hintFor, summaryLine } from "../similarityLogic.js";

const TEXT = "Intro line.\nUnpaid care work limits how many hours women can spend in paid employment each week.";
const START = TEXT.indexOf("Unpaid");
const END = TEXT.length;

test("a near copy says how close it is to the named source, and offers quote or own words", () => {
  assert.equal(
    hintFor("near_verbatim", "Kumar 2021"),
    "Very close to Kumar 2021. Quote it with a citation, or put it in your own words.",
  );
});

test("a reworded copy is called close, with the same two options", () => {
  assert.equal(
    hintFor("close_paraphrase", "Kumar 2021"),
    "Close to Kumar 2021. Quote it with a citation, or put it in your own words.",
  );
});

test("an uncited quotation names its source when there is one, and asks for a citation in the paragraph", () => {
  assert.equal(
    hintFor("quote_without_citation", "Kumar 2021"),
    "Quoted from Kumar 2021 without a citation. Add a citation in the same paragraph, or put it in your own words.",
  );
  assert.equal(
    hintFor("quote_without_citation", null),
    "Quoted without a citation. Add a citation in the same paragraph, or put it in your own words.",
  );
});

test("a flag with no source title falls back to a neutral name, never an id", () => {
  assert.equal(hintFor("near_verbatim", null), "Very close to a project source. Quote it with a citation, or put it in your own words.");
});

test("no check means no flags and no claim that the section was checked", () => {
  assert.deepEqual(flagsFor(null, "v1", TEXT), { state: "none", flags: [] });
});

test("a check of an older version shows no flags, because its offsets no longer match the text", () => {
  const check = { version_id: "v0", findings: [{ start: START, end: END, kind: "near_verbatim", source_title: "Kumar 2021" }] };

  assert.deepEqual(flagsFor(check, "v1", TEXT), { state: "older", flags: [] });
});

test("a check of the current version quotes each flagged sentence from the saved text, in order", () => {
  const check = {
    version_id: "v1",
    findings: [
      { start: START, end: END, kind: "close_paraphrase", source_title: "Kumar 2021" },
      { start: 0, end: 11, kind: "quote_without_citation", source_title: null },
    ],
  };

  const result = flagsFor(check, "v1", TEXT);

  assert.equal(result.state, "current");
  assert.deepEqual(result.flags.map((flag) => flag.quote), [TEXT.slice(START, END), "Intro line."]);
  assert.deepEqual(result.flags.map((flag) => flag.kind), ["close_paraphrase", "quote_without_citation"]);
  assert.match(result.flags[1].hint, /^Quoted without a citation\./);
});

test("a clean check says so in words, and lists the kinds that occurred", () => {
  assert.equal(summaryLine({ near_verbatim: 0, close_paraphrase: 0, quote_without_citation: 0 }), "No flags.");
  assert.equal(
    summaryLine({ near_verbatim: 1, close_paraphrase: 2, quote_without_citation: 0 }),
    "3 flags: 1 very close, 2 close paraphrases",
  );
});

import { institutionalCheck } from "../similarityLogic.js";

test("the institutional check is disabled with the server's hint while it is not connected", () => {
  const providers = [
    { id: "project_sources", label: "Project sources", available: true, hint: null },
    { id: "institutional", label: "Institutional check", available: false, hint: "Not connected: ask your institution" },
  ];
  assert.deepEqual(institutionalCheck(providers), { label: "Institutional check", disabled: true, hint: "Not connected: ask your institution" });
});

test("the institutional check is enabled when the server reports it available, and falls back to the fixed hint when the list is missing", () => {
  assert.deepEqual(institutionalCheck([{ id: "institutional", label: "Institutional check", available: true, hint: null }]),
    { label: "Institutional check", disabled: false, hint: "" });
  assert.deepEqual(institutionalCheck(null), { label: "Institutional check", disabled: true, hint: "Not connected: ask your institution" });
});
