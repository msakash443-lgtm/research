import test from "node:test";
import assert from "node:assert/strict";
import { previousStep, staleForStep, stepHref } from "../steps/frameLogic.js";

test("Ask returns Sources as its previous built step", () => {
  assert.deepEqual(previousStep("ask"), { id: "sources", title: "Sources" });
});

test("unbuilt steps are not used as previous navigation targets", () => {
  assert.equal(previousStep("verification"), null);
});

test("step links point to the requested project step", () => {
  assert.equal(stepHref("project/1", "sources"), "#/p/project/1/sources");
});

test("stale artifacts are mapped to the screens that need redoing", () => {
  const artifacts = [
    { stage: "retrieved", id: "source-result" },
    { stage: "synthesized", id: "answer" },
    { stage: "screened", id: "screening" },
  ];
  assert.deepEqual(staleForStep("sources", artifacts), [artifacts[0]]);
  assert.deepEqual(staleForStep("ask", artifacts), [artifacts[1]]);
  assert.deepEqual(staleForStep("criteria", artifacts), []);
});
