import test from "node:test";
import assert from "node:assert/strict";

class Node {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.dataset = {};
  }

  append(...children) {
    this.children.push(...children);
  }

  replaceChildren(...children) {
    this.children = children;
  }

  querySelector() {
    return null;
  }

  addEventListener() {}

  setAttribute(name, value) {
    this[name] = value;
  }

  get childElementCount() {
    return this.children.length;
  }
}

globalThis.document = {
  createElement(tag) {
    return new Node(tag);
  },
};

function descendants(node) {
  return [node, ...node.children.filter((child) => child instanceof Node).flatMap(descendants)];
}

function fakeResponse(body) {
  return { ok: true, status: 200, text: async () => JSON.stringify(body) };
}

const scopeStep = (await import("../steps/scope.js")).default;

const TYPES = [{ name: "full_journey", label: "Full journey", stages: ["idea", "scoped"] }];

const EMPTY_BRIEF = {
  exists: false, problem: "", why_it_matters: "", risks: null, selected_idea_id: null,
  feasibility: [], open_items: ["problem", "why_it_matters"], blockers: [],
  ready_for_g1: false, ideas: [], note: "Complete the required fields.",
};

function fetchFor(brief = EMPTY_BRIEF) {
  return async (path) => {
    if (path.endsWith("/idea-brief")) return fakeResponse(brief);
    if (path.includes("/discipline-profiles")) return fakeResponse([]);
    if (path.includes("/profile")) return fakeResponse({ discipline: null, effective: {} });
    if (path.includes("/research-types")) return fakeResponse(TYPES);
    throw new Error(`unexpected fetch: ${path}`);
  };
}

function ctxFor(researchType, { canWrite = true, context = [] } = {}) {
  return {
    project: { id: "project-1" },
    cache: {},
    canWrite,
    isOwner: canWrite,
    summary: {
      context,
      gates: [],
      stage: { stage: "idea", research_type: researchType },
    },
  };
}

test("Save research type is disabled with a visible reason when the picked type is already saved (X.163)", async () => {
  globalThis.fetch = fetchFor();
  const root = new Node("main");
  await scopeStep.render(root, ctxFor("full_journey"));

  const save = descendants(root).find((n) => n.tagName === "button" && n.textContent === "Save research type");
  assert.ok(save, "expected the Save research type button");
  assert.equal(save.disabled, true);
  const note = descendants(root).find((n) => n.tagName === "span" && n.textContent === "Already saved.");
  assert.ok(note, "expected a visible 'Already saved.' reason next to the disabled button");
});
test("Scope separates objectives from structured context and exposes the existing objective writer", async () => {
  globalThis.fetch = fetchFor();
  const root = new Node("main");
  await scopeStep.render(root, ctxFor("full_journey", {
    context: [
      { id: "objective-1", kind: "objective", content: "Measure the outcome" },
      { id: "method-1", kind: "methodology", content: "Use interviews" },
    ],
  }));
  const objectivePanel = descendants(root).find((node) => node.className?.includes("objectives-panel"));
  const contextPanel = descendants(root).find((node) => node.className?.includes("context-panel"));
  const panelText = (panel) => descendants(panel).map((node) => node.textContent || "").join(" ");
  assert.match(panelText(objectivePanel), /Measure the outcome/);
  assert.doesNotMatch(panelText(contextPanel), /Measure the outcome/);
  assert.match(panelText(contextPanel), /Use interviews/);
  assert.ok(descendants(objectivePanel).some((node) => node.textContent === "Add objective"));
});

test("Read-only Scope retains landscape lookup while hiding brief and context writers", async () => {
  globalThis.fetch = fetchFor({
    ...EMPTY_BRIEF, exists: true, problem: "A stated problem", why_it_matters: "A reason",
    feasibility: [{ item: "method_fit", status: "concern", note: "Need a collaborator." }],
  });
  const root = new Node("main");
  await scopeStep.render(root, ctxFor("full_journey", { canWrite: false }));
  const texts = descendants(root).map((node) => node.textContent || "");
  assert.ok(texts.includes("Run landscape scan"));
  assert.ok(!texts.includes("Save idea brief"));
  assert.ok(!texts.includes("Add objective"));
  assert.ok(!texts.includes("Add context"));
  assert.ok(texts.includes("Method fit: Concern — Need a collaborator."));
});
