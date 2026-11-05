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

function fetchFor({ researchType }) {
  return async (path) => {
    if (path.includes("/discipline-profiles")) return fakeResponse([]);
    if (path.includes("/profile")) return fakeResponse({ discipline: null, effective: {} });
    if (path.includes("/research-types")) return fakeResponse(TYPES);
    throw new Error(`unexpected fetch: ${path}`);
  };
}

function ctxFor(researchType) {
  return {
    project: { id: "project-1" },
    cache: {},
    canWrite: true,
    isOwner: true,
    summary: {
      context: [],
      gates: [],
      stage: { stage: "idea", research_type: researchType },
    },
  };
}

test("Save research type is disabled with a visible reason when the picked type is already saved (X.163)", async () => {
  globalThis.fetch = fetchFor({ researchType: "full_journey" });
  const root = new Node("main");
  await scopeStep.render(root, ctxFor("full_journey"));

  const save = descendants(root).find((n) => n.tagName === "button" && n.textContent === "Save research type");
  assert.ok(save, "expected the Save research type button");
  assert.equal(save.disabled, true);
  const note = descendants(root).find((n) => n.tagName === "span" && n.textContent === "Already saved.");
  assert.ok(note, "expected a visible 'Already saved.' reason next to the disabled button");
});
