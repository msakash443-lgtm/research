import test from "node:test";
import assert from "node:assert/strict";

class Node {
  constructor(tag) { this.tagName = tag; this.children = []; this.dataset = {}; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  addEventListener() {}
  setAttribute(name, value) { this[name] = value; }
  get childElementCount() { return this.children.length; }
}
globalThis.document = { createElement: (tag) => new Node(tag) };
const overview = (await import("../steps/overview.js")).default;
const text = (node) => [node.textContent || "", ...node.children.filter((n) => n instanceof Node).map(text)].join(" ");

test("overview puts unfinished screening before G3 without hiding the gate condition", async () => {
  const gate = { id: "EC-4.1", level: "required", met: false, message: "G3 is not approved", fix: "Screening: approve G3" };
  const screening = { id: "EC-4.3", level: "recommended", met: false, message: "2 records unscreened", fix: "Screening: queue" };
  const root = new Node("main");
  const ctx = {
    project: { id: "project-1" }, cache: { guide: { stages: { retrieved: { title: "Records retrieved", meaning: "Records saved", advice: "Prepare" } } } },
    summary: { sources: [{ id: "s1" }], gates: [{ code: "G3", status: "pending", can_decide: true }], stage: {
      stage: "retrieved", next: [{ stage: "screened", gate: "G3", gate_status: "pending", conditions: [gate, screening] }],
      suggestions: [
        { condition_id: gate.id, level: gate.level, message: gate.message, step: "screen" },
        { condition_id: screening.id, level: screening.level, message: screening.message, step: "screen" },
      ],
    } },
  };
  await overview.render(root, ctx);
  const content = text(root);
  assert.ok(content.indexOf("2 records unscreened") < content.indexOf("G3 is not approved"));
  assert.match(content, /Screen records and resolve outstanding screening reviews/);
  assert.match(content, /Before you move on/);
  assert.doesNotMatch(content, /Waiting on you/);
  ctx.summary.stage.next[0].conditions = [gate, { ...screening, met: true }];
  ctx.summary.stage.suggestions = [ctx.summary.stage.suggestions[0]];
  await overview.render(root, ctx);
  assert.match(text(root), /Waiting on you/);
  ctx.summary.sources = [];
  await overview.render(root, ctx);
  assert.match(text(root), /Add or retrieve records before screening/);
  assert.doesNotMatch(text(root), /Waiting on you/);
});
