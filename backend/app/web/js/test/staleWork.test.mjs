import test from "node:test";
import assert from "node:assert/strict";

class Node {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
  }

  append(...children) {
    this.children.push(...children);
  }

  setAttribute(name, value) {
    this[name] = value;
  }
}

globalThis.document = {
  createElement(tag) {
    return new Node(tag);
  },
};

const { staleWorkBanner } = await import("../steps/frame.js");

function descendants(node) {
  return [node, ...node.children.filter((child) => child instanceof Node).flatMap(descendants)];
}

test("stale research runs render a Needs redoing banner on Ask", () => {
  const banner = staleWorkBanner({
    summary: { staleArtifacts: [{ kind: "research_run", stale_reason: "Earlier stage was reopened." }] },
  }, "ask");

  assert.equal(banner.className, "next-step stale-work");
  assert.ok(descendants(banner).some((node) => node.textContent === "Needs redoing"));
  assert.ok(descendants(banner).some((node) => node.textContent === "Earlier stage was reopened."));
});

test("unrelated stale artifacts do not render on Ask", () => {
  const banner = staleWorkBanner({
    summary: { staleArtifacts: [{ kind: "search_run", stale_reason: "Search changed." }] },
  }, "ask");

  assert.equal(banner, null);
});
