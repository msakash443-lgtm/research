import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { answerStageQuestion, stageAssistantState } from "../stageAssistantLogic.js";

const summary = {
  stage: { stage: "extracted", chain: [{ stage: "extracted", label: "Evidence extraction" }], next: [{ stage: "synthesized", gate: "G4", ready: false }] },
  gates: [{ code: "G4", status: "pending" }],
};

test("stage assistant explains the local current stage without a model", () => {
  const state = stageAssistantState(summary, []);
  assert.match(answerStageQuestion("What should I do at this stage?", state), /relevant full text/i);
});

test("stage assistant explains the next human decision without approving it", () => {
  const state = stageAssistantState(summary, []);
  const answer = answerStageQuestion("How do I proceed?", state);
  assert.match(answer, /G4 remains a human decision/);
  assert.match(answer, /Review the current stage requirements/);
});

test("assistant panel does not call an assistant endpoint", () => {
  const app = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "..", "..", "app.js"), "utf8");
  assert.doesNotMatch(app, /\/assistant(?:\/|["`])/);
});

test("assistant launcher stays clickable in the bottom-right UI with a smooth panel", () => {
  const webDir = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
  const markup = readFileSync(join(webDir, "index.html"), "utf8");
  const styles = readFileSync(join(webDir, "styles.css"), "utf8");
  assert.match(markup, /id="assistant-button"[^>]*assistant-launcher[\s\S]*assistant-mascot\.png/);
  assert.match(styles, /\.assistant-launcher \{ position:fixed;[\s\S]*right:20px;[\s\S]*bottom:20px/);
  assert.match(styles, /\.assistant-panel\.is-open \{ opacity:1;[\s\S]*transform:translateY\(0\)/);
  assert.match(markup, /assistant-ideas-tab[\s\S]*assistant-notes-tab/);
});
