import test from "node:test";
import assert from "node:assert/strict";
import { synonymSuggestionPanelOptions, synonymSuggestionRequest } from "../synonymSuggestLogic.js";

test("Search synonym action posts the concept block and forwards the returned target", () => {
  const block = { label: "remote work", terms: ["telework", "home working"] };
  const requestSpec = synonymSuggestionRequest("project/1", block);
  assert.equal(requestSpec.path, "/api/projects/project%2F1/searches/suggest-synonyms");
  assert.equal(requestSpec.options.method, "POST");
  assert.deepEqual(JSON.parse(requestSpec.options.body), { block });

  const panel = synonymSuggestionPanelOptions({ target_id: "target-123" });
  assert.deepEqual(panel, { targetKind: "synonym", targetId: "target-123" });
  assert.deepEqual(block.terms, ["telework", "home working"]);
});
