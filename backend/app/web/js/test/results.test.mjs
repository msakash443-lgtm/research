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

function fakeResponse(body) {
  return { ok: true, status: 200, text: async () => JSON.stringify(body) };
}

const resultsStep = (await import("../steps/results.js")).default;

test("Results with search results renders a link to Sources", async () => {
  globalThis.fetch = async () => {
    throw new Error("network unavailable in this test");
  };
  const root = new Node("main");
  await resultsStep.render(root, {
    project: { id: "project-1" },
    cache: {},
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

test("the search-results box lists records ranked/filtered by citation count (M2.13.2)", async () => {
  globalThis.fetch = async () =>
    fakeResponse({
      search_id: "s1",
      version: 1,
      results: [
        { title: "A", year: 2020, cited_by_count: 5, citation_count_unknown: false },
        { title: "B", year: 2019, cited_by_count: null, citation_count_unknown: true },
        { title: "C", year: 2022, cited_by_count: 20, citation_count_unknown: false },
      ],
    });
  const root = new Node("main");
  await resultsStep.render(root, {
    project: { id: "project-2" },
    cache: {},
    summary: {
      seeds: [],
      prisma: {
        identification: {
          identified: 3, duplicates_removed: 0, after_duplicates_removed: 3,
          searches: [{ search_id: "s1", database: "openalex", version: 1, identified: 3, truncated: false, exact: true }],
        },
        warnings: [],
      },
      knownItems: null,
      staleArtifacts: [],
      stage: { next: [] },
    },
    canWrite: true,
  });

  const all = descendants(root);
  const stats = all.find((n) => n.tagName === "p" && String(n.textContent).includes("unknown citation count"));
  assert.ok(stats, "expected a hidden/unknown citation stats line");
  assert.equal(stats.textContent, "0 hidden below the citation threshold · 1 unknown citation count · 3 of 3 shown");

  const resultsTable = all.filter((n) => n.tagName === "table")[1]; // [0] is the PRISMA counts table
  const dataRows = resultsTable.children.slice(1); // first row is the table header
  const citationCells = dataRows.map((row) => row.children[3].textContent);
  assert.deepEqual(citationCells, ["5", "Unknown", "20"]);
});
