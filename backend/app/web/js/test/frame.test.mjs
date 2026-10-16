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

test("the Ask step renders the shared frame with a Previous step link to Sources", () => {
  const root = new Node("main");
  askStep.render(root, {
    project: { id: "project-1" },
    summary: { stage: { next: [] }, staleArtifacts: [], runs: [] },
    isOwner: false,
    canWrite: false,
  });

  const footer = root.children.find((node) => node.className === "step-footer");
  assert.ok(footer);
  const previous = footer.children.find((node) => node.className === "back-link");
  assert.equal(previous.textContent, "Previous step: Sources");
  assert.equal(previous.href, "#/p/project-1/sources");
});
