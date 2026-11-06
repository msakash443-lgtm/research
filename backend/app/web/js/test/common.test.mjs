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

  addEventListener(type, handler) {
    this._listeners = this._listeners || {};
    this._listeners[type] = handler;
  }

  setAttribute(name, value) {
    this[name] = value;
  }

  get childElementCount() {
    return this.children.length;
  }
}

const toastNode = { textContent: "", classList: { add() {}, remove() {} } };
globalThis.document = {
  createElement(tag) {
    return new Node(tag);
  },
  querySelector(selector) {
    return selector === "#toast" ? toastNode : null;
  },
};
globalThis.window = { setTimeout: () => {}, prompt: () => null };

function fakeResponse(status, body) {
  return { ok: status < 400, status, text: async () => JSON.stringify(body) };
}

function descendants(node) {
  return [node, ...node.children.filter((child) => child instanceof Node).flatMap(descendants)];
}

const { advanceCard } = await import("../steps/common.js");

const RECOMMENDED = [
  { id: "EC-2.2", level: "recommended", met: false, message: "No criterion yet for: intervention, comparison, outcome.", fix: null },
];

function makeCtx(refreshCalls) {
  return {
    project: { id: "project-1" },
    summary: { gates: [] },
    canWrite: true,
    refresh: async () => refreshCalls.push(true),
  };
}

test("an unmet Recommended condition prompts for a reason and resends with override_reasons (X.165)", async () => {
  const calls = [];
  globalThis.fetch = async (path, options = {}) => {
    calls.push({ path, options });
    if (calls.length === 1) return fakeResponse(409, { detail: { message: "locked", recommended: RECOMMENDED, required: [] } });
    return fakeResponse(200, { from_stage: "scoped", to_stage: "search_planned", gate: null });
  };
  globalThis.window.prompt = () => "Completed the criteria instead of overriding it.";

  const refreshCalls = [];
  const card = advanceCard(makeCtx(refreshCalls), { stage: "search_planned", ready: true, gate: null, gate_status: null });
  const button = descendants(card).find((n) => n.tagName === "button");
  assert.ok(button);
  await button._listeners.click();

  assert.equal(calls.length, 2);
  assert.equal(calls[0].options.body, JSON.stringify({ stage: "search_planned" }));
  const secondBody = JSON.parse(calls[1].options.body);
  assert.deepEqual(secondBody, {
    stage: "search_planned",
    override_reasons: { "EC-2.2": "Completed the criteria instead of overriding it." },
  });
  assert.equal(refreshCalls.length, 1);
  assert.equal(button.disabled, false);
});

test("cancelling the reason prompt sends no retry and re-enables the button (X.165)", async () => {
  const calls = [];
  globalThis.fetch = async (path, options = {}) => {
    calls.push({ path, options });
    return fakeResponse(409, { detail: { message: "locked", recommended: RECOMMENDED, required: [] } });
  };
  globalThis.window.prompt = () => null;

  const refreshCalls = [];
  const card = advanceCard(makeCtx(refreshCalls), { stage: "search_planned", ready: true, gate: null, gate_status: null });
  const button = descendants(card).find((n) => n.tagName === "button");
  await button._listeners.click();

  assert.equal(calls.length, 1, "no retry should be sent once the researcher cancels");
  assert.equal(refreshCalls.length, 0);
  assert.equal(button.disabled, false);
});

test("a 409 with only unmet Required conditions shows the server message, no prompt (X.165)", async () => {
  const calls = [];
  globalThis.fetch = async (path, options = {}) => {
    calls.push({ path, options });
    return fakeResponse(409, {
      detail: { message: "Required conditions unmet", recommended: [], required: [{ id: "EC-1.1", level: "required", met: false, message: "x", fix: null }] },
    });
  };
  let promptCalled = false;
  globalThis.window.prompt = () => {
    promptCalled = true;
    return "should not be used";
  };

  const refreshCalls = [];
  const card = advanceCard(makeCtx(refreshCalls), { stage: "search_planned", ready: true, gate: null, gate_status: null });
  const button = descendants(card).find((n) => n.tagName === "button");
  await button._listeners.click();

  assert.equal(calls.length, 1);
  assert.equal(promptCalled, false, "Required conditions cannot be overridden, so no reason prompt is shown");
  assert.equal(refreshCalls.length, 0);
  assert.equal(button.disabled, false);
});
