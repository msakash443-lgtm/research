// Run with: node --test backend/app/web/js/test   (no packages; pytest does not run these)
import test from "node:test";
import assert from "node:assert/strict";
import { CITE_CHAR, citableSources, citedIdsIn, pickerMatches, sentenceRanges, sourceLabel, unlinkedStatements } from "../sourcePanelLogic.js";

const SOURCES = [
  { id: "a", title: "Wage gaps in Ghana", authors: ["Mensah Kofi"], year: 2021, metadata_verified: true, merged_into: null },
  { id: "b", title: "Unchecked preprint", authors: ["Doe Jane"], year: 2024, metadata_verified: false, merged_into: null },
  { id: "c", title: "Care work and hours", authors: ["Lee Ann", "Roy Sam"], year: 2019, metadata_verified: true, merged_into: null },
  { id: "d", title: "Old duplicate", authors: [], year: 2010, metadata_verified: true, merged_into: "a" },
];

test("only verified, unmerged sources are offered for citation", () => {
  assert.deepEqual(citableSources(SOURCES).map((s) => s.id), ["c", "a"]);
});

test("the picker filters verified sources by title, author or year", () => {
  assert.deepEqual(pickerMatches(SOURCES, "ghana").map((s) => s.id), ["a"]);
  assert.deepEqual(pickerMatches(SOURCES, "2019").map((s) => s.id), ["c"]);
  assert.deepEqual(pickerMatches(SOURCES, "preprint"), []);
});

test("the picker returns at most the limit", () => {
  assert.equal(pickerMatches(SOURCES, "", 1).length, 1);
});

test("a source is labelled by first author's surname and year, else its title", () => {
  assert.equal(sourceLabel(SOURCES[0]), "Kofi 2021");
  assert.equal(sourceLabel({ title: "Anon report", authors: [], year: null }), "Anon report");
});

test("sentences split on terminators followed by a space, and on line breaks", () => {
  const flat = "One thing. Two thing!\nThree thing?";
  assert.deepEqual(sentenceRanges(flat).map(({ from, to }) => flat.slice(from, to)), ["One thing.", "Two thing!", "Three thing?"]);
});

test("a decimal point does not end a sentence", () => {
  const flat = "The rate was 3.5 percent in 2020.";
  assert.equal(sentenceRanges(flat).length, 1);
});

test("a sentence with a number and no citation is flagged, with offsets into the block", () => {
  const flat = `Women earn less in 2020. Men earn more${CITE_CHAR}. Hours rose 12%.`;
  const flagged = unlinkedStatements(flat).map(({ from, to }) => flat.slice(from, to));
  assert.deepEqual(flagged, ["Women earn less in 2020.", "Hours rose 12%."]);
});

test("a sentence with a citation is not flagged even if it states a number", () => {
  const flat = `Hours rose 12%${CITE_CHAR}.`;
  assert.deepEqual(unlinkedStatements(flat), []);
});

test("a sentence without any number is never flagged", () => {
  assert.deepEqual(unlinkedStatements("Care work is unpaid and undervalued."), []);
});

test("cited source ids are listed once, in order", () => {
  assert.deepEqual(citedIdsIn(["a", "c", "a", null, ""]), ["a", "c"]);
});
