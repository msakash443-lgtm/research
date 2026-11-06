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

const askStep = (await import("../steps/ask.js")).default;
test("the Ask step renders the shared frame with a Previous step link to Verify extraction", () => {
  const root = new Node("main");
  askStep.render(root, {
    project: { id: "project-1" },
    summary: { stage: { next: [] }, staleArtifacts: [], runs: [] },
    isOwner: false,
    canWrite: false,
  });

  const footer = root.children.find((node) => node.className === "step-footer");
  const previous = footer.children.find((node) => node.className === "back-link");
  assert.ok(footer);
  assert.equal(previous.textContent, "Previous step: Verify extraction");
  assert.equal(previous.href, "#/p/project-1/verification");
});

test("condition fixes render with the level, exact message and accessible destination", async () => {
  const root = new Node("main");
  const ctx = {
    project: { id: "project-2" },
    summary: {
      stage: {
        next: [{
          stage: "search_planned",
          ready: false,
          conditions: [{
            id: "EC-2.1",
            level: "required",
            met: false,
            message: "G2 is not approved yet",
            fix: "Criteria: approve G2",
          }],
        }],
      },
      staleArtifacts: [],
    },
  };
  const workNode = new Node("div");
  const { renderFrame, conditionFixLink } = await import("../steps/frame.js");
  renderFrame(root, ctx, { stepId: "criteria", work: [workNode], next: ["search_planned"] });
  assert.ok(root.children.includes(workNode));
  const condition = root.children[0].children[1].children[0];
  assert.equal(condition.children[0].textContent, "Must do");
  assert.equal(condition.children[1].textContent, " G2 is not approved yet");
  const link = condition.children[3];
  assert.equal(link.textContent, "Fix this");
  assert.equal(link.href, "#/p/project-2/criteria");
  assert.equal(link["aria-label"], "Fix: G2 is not approved yet");
  assert.deepEqual(conditionFixLink(ctx, { fix: "Results check: open the missed-papers list" }), {
    href: "#/p/project-2/results",
    text: "Fix this",
  });
  assert.deepEqual(conditionFixLink(ctx, { fix: "Extraction: Start extraction" }), {
    href: "#/p/project-2/evidence",
    text: "Fix this",
  });
  assert.equal(conditionFixLink(ctx, { fix: "Redo list: open each stale item on its own screen" }), null);
});

