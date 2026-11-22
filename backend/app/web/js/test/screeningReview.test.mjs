import test from "node:test";
import assert from "node:assert/strict";

class Node {
  constructor(tag) { this.tagName = tag; this.children = []; this.dataset = {}; }
  append(...children) { this.children.push(...children); }
  setAttribute(name, value) { this[name] = value; }
}
globalThis.document = { createElement: (tag) => new Node(tag) };
const { reviewNode, reviewSummary } = await import("../steps/screeningReview.js");
const text = (node) => (typeof node === "string" ? node : [node.textContent || "", ...node.children.map(text)].join(" "));
const find = (node, predicate) => (predicate(node) ? [node] : node.children.flatMap((child) => find(child, predicate)));

const data = {
  locked: false, dual_screen_enabled: false, passed_title_abstract: 3, awaiting_full_text: 1,
  items: [{ source_id: "s1", title: "Wage effects", authors: ["A. Author"], year: 2020, doi: "10.1/x", abstract: "Panel data.", metadata_verified: false }],
};

test("the G3 review states what is included and what still waits, with the source's evidence inside the page", () => {
  assert.match(reviewSummary(data), /1 record is included at full text\. 1 of 3 that passed title\/abstract still has no full-text decision\./);
  const panel = reviewNode({ project: { id: "p9" } }, data);
  const content = text(panel);
  assert.match(content, /Wage effects \(2020\)/);
  assert.match(content, /Panel data\./);
  assert.match(content, /Metadata not verified/);
  const links = find(panel, (n) => n.tagName === "a").map((n) => n.href);
  assert.ok(links.includes("https://doi.org/10.1/x"));
  assert.ok(links.includes("#/p/p9/screen") && links.includes("#/p/p9/overview"));
  assert.equal(find(panel, (n) => n.tagName === "button").length, 0, "review never approves");
});
