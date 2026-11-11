import test from "node:test";
import assert from "node:assert/strict";

class Node {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
  }

  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  addEventListener() {}
  setAttribute(name, value) { this[name] = value; }
  get childElementCount() { return this.children.length; }
}

globalThis.document = { createElement: (tag) => new Node(tag) };

function descendants(node) {
  return [node, ...node.children.filter((child) => child instanceof Node).flatMap(descendants)];
}

const response = (body) => ({ ok: true, status: 200, text: async () => JSON.stringify(body) });
const gapsStep = (await import("../steps/gaps.js")).default;

test("read-only Gaps screen keeps critique visible and hides every mutation control", async () => {
  globalThis.fetch = async (path) => {
    if (path.endsWith("/gaps")) return response([{
      id: "gap-1", type: "empirical", statement: "Evidence gap", supporting_paper_ids: ["source-1", "source-2"],
      matrix_cell_ids: [], future_research: [], priority: 2, selected: true,
    }]);
    if (path.endsWith("/research-questions")) return response([{
      id: "question-1", text: "How does the intervention affect the outcome?", type: "explanatory", gap_ids: ["gap-1"],
    }]);
    if (path.endsWith("/hypotheses")) return response([{
      id: "hypothesis-1", research_question_id: "question-1", statement: "The outcome improves.",
      direction: "positive", theoretical_basis: "Theory", empirical_support: "Prior evidence",
    }]);
    if (path.endsWith("/sources")) return response([
      { id: "source-1", title: "First source" }, { id: "source-2", title: "Second source" },
    ]);
    throw new Error(`unexpected fetch: ${path}`);
  };

  const root = new Node("main");
  await gapsStep.render(root, {
    project: { id: "project-1" }, canWrite: false, role: "viewer", cache: {}, refresh: async () => {},
    summary: {
      gates: [], staleArtifacts: [],
      stage: { chain: [{ stage: "extracted" }, { stage: "gaps_selected" }], next: [] },
    },
  });
  const nodes = descendants(root);
  const text = nodes.map((node) => node.textContent || "").join(" ");
  const buttons = nodes.filter((node) => node.tagName === "button").map((node) => node.textContent);
  assert.match(text, /Evidence gap/);
  assert.match(text, /How does the intervention affect the outcome\?/);
  assert.match(text, /The outcome improves\./);
  assert.ok(buttons.includes("Run FINER critique"));
  for (const action of ["Add gap", "Save curation", "Add research question", "Save question", "Add hypothesis", "Save hypothesis"]) {
    assert.ok(!buttons.includes(action), `${action} must be hidden from read-only roles`);
  }
  assert.ok(!nodes.some((node) => node.tagName === "details"), "edit disclosures must be hidden from read-only roles");
});
