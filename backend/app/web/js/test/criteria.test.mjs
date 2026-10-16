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

const criteriaStep = (await import("../steps/criteria.js")).default;

test("Criteria renders G2 decisions, EC-2 condition, and journey navigation", () => {
  const root = new Node("main");
  criteriaStep.render(root, {
    project: { id: "project-1" },
    summary: {
      criteria: { framework: "pico", locked: false, problems: [], criteria: [] },
      seeds: [],
      staleArtifacts: [],
      gates: [{
        code: "G2",
        status: "pending",
        can_decide: true,
        can_reopen: false,
        waiting_for: [],
        required_roles: ["owner", "supervisor"],
      }],
      stage: {
        next: [{
          stage: "search_planned",
          ready: false,
          gate: "G2",
          gate_status: "pending",
          conditions: [{
            id: "EC-2.1",
            level: "required",
            met: false,
            message: "G2 is not approved yet",
            fix: "Criteria: approve G2",
          }],
        }],
      },
    },
    canWrite: true,
    isOwner: false,
  });

  const nodes = descendants(root);
  const texts = nodes.map((node) => node.textContent).filter(Boolean);
  assert.ok(texts.includes("Approve"));
  assert.ok(texts.includes("Reject"));
  assert.ok(texts.includes("Before you move on"));
  assert.ok(texts.includes("Next: Search Planned"));
  const previous = nodes.find((node) => node.className === "back-link");
  assert.equal(previous.textContent, "Previous step: Scope");
  assert.equal(previous.href, "#/p/project-1/scope");
});
