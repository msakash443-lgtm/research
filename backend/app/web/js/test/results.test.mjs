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

function descendants(node) {
  return [node, ...node.children.filter((child) => child instanceof Node).flatMap(descendants)];
}

globalThis.document = {
  createElement(tag) {
    return new Node(tag);
  },
};

const resultsStep = (await import("../steps/results.js")).default;

test("Results with search results renders a link to Sources", () => {
  const root = new Node("main");
  resultsStep.render(root, {
    project: { id: "project-1" },
    summary: {
      seeds: [],
      prisma: { identification: { identified: 1, duplicates_removed: 0, after_duplicates_removed: 1, searches: [{ database: "openalex", version: 1, identified: 1, truncated: false, exact: true }] }, warnings: [] },
      knownItems: null,
      staleArtifacts: [],
      stage: { next: [{ stage: "retrieved", ready: true, conditions: [] }] },
    },
    canWrite: true,
  });

  const sources = descendants(root).find((node) => node.tagName === "a" && node.textContent === "Go to Sources");
  assert.ok(sources);
  assert.equal(sources.href, "#/p/project-1/sources");
});
