import test from "node:test";
import assert from "node:assert/strict";
import {
  FEASIBILITY_FIELDS,
  feasibilityByItem,
  finerStatusCounts,
  gapEvidenceReady,
  gapPayload,
  hypothesisPayload,
  ideaBriefPayload,
  landscapeScanPath,
  researchQuestionPayload,
  selectedGapIds,
  objectiveBody,
  splitScopeContext,
} from "../strategyLogic.js";

test("Scope takes objectives from the objectives records and never shows a legacy objective context item", () => {
  const split = splitScopeContext(
    [
      { id: "idea", kind: "idea", content: "Project idea" },
      { id: "legacy", kind: "objective", content: "Legacy copy" },
      { id: "decision", kind: "decision", content: "Use interviews" },
    ],
    [
      { id: "objective", statement: "Measure the outcome" },
      { id: "retired", statement: "Old objective", retired_at: "2026-01-01" },
    ],
  );
  assert.deepEqual(split.objectives.map((item) => item.id), ["objective"]);
  assert.deepEqual(split.context.map((item) => item.id), ["decision"]);
});

test("An objective correction sends only the changed fields, and blank optional fields become null", () => {
  const existing = { statement: "Map output", measure: "Papers per year", deliverable: null, target_date: "2027-01-01", rationale: null };
  const values = { statement: "Map output ", measure: "", deliverable: "", target_date: "2027-01-01", rationale: "" };
  assert.deepEqual(objectiveBody(existing, values), { measure: null });
  assert.deepEqual(objectiveBody(null, values), {
    statement: "Map output", rationale: null, measure: null, deliverable: null, target_date: "2027-01-01",
  });
});

test("Idea brief saves preserve every feasibility item and leave unanswered items explicitly unsure", () => {
  const feasibility = feasibilityByItem([
    { item: "method_fit", status: "concern", note: "Need a collaborator." },
  ]);
  const payload = ideaBriefPayload({
    problem: "  A focused problem  ",
    whyItMatters: "  It matters  ",
    risks: "  ",
    selectedIdeaId: "",
    feasibility,
  });
  assert.equal(payload.problem, "A focused problem");
  assert.equal(payload.why_it_matters, "It matters");
  assert.equal(payload.risks, null);
  assert.equal(payload.selected_idea_id, null);
  assert.equal(payload.feasibility.length, FEASIBILITY_FIELDS.length);
  assert.deepEqual(payload.feasibility.find((item) => item.item === "method_fit"), {
    item: "method_fit", status: "concern", note: "Need a collaborator.",
  });
  assert.deepEqual(payload.feasibility.find((item) => item.item === "ethics_risk"), {
    item: "ethics_risk", status: "unsure", note: "",
  });
});

test("Landscape scan keeps the entered query in one encoded query parameter", () => {
  const path = landscapeScanPath("project-1", "  child & adult/β  ");
  const url = new URL(path, "https://research.example");
  assert.equal(url.pathname, "/api/projects/project-1/idea-brief/landscape-scan");
  assert.equal(url.searchParams.get("query"), "child & adult/β");
  assert.deepEqual([...url.searchParams.keys()], ["query"]);
});

test("Gap evidence requires two distinct papers and creates an auditable evidence payload", () => {
  assert.equal(gapEvidenceReady(["paper-a", "paper-a"]), false);
  assert.equal(gapEvidenceReady(["paper-a", "paper-b", "paper-b"]), true);
  assert.deepEqual(gapPayload({
    type: "empirical", statement: "  Missing populations  ",
    supportingPaperIds: ["paper-a", "paper-a", "paper-b"], priority: "4",
  }), {
    type: "empirical", statement: "Missing populations",
    supporting_paper_ids: ["paper-a", "paper-b"], matrix_cell_ids: [],
    future_research: [], priority: 4,
  });
});

test("Research questions link only the selected gap IDs supplied by their editor", () => {
  const selected = selectedGapIds([
    { id: "gap-a", selected: true }, { id: "gap-b", selected: false },
  ]);
  assert.deepEqual(selected, ["gap-a"]);
  assert.deepEqual(researchQuestionPayload({
    text: "  Does the intervention work? ", type: " explanatory ", gapIds: selected,
  }), { text: "Does the intervention work?", type: "explanatory", gap_ids: ["gap-a"] });
});

test("FINER summary preserves advisory status counts without implying approval", () => {
  assert.deepEqual(finerStatusCounts({ items: [
    { status: "needs_human_review" }, { status: "needs_human_review" },
    { status: "linked_to_selected_gaps" }, { status: "missing_gap_link" },
  ] }), { needsHumanReview: 2, linkedToSelectedGaps: 1, missingGapLink: 1 });
});

test("Hypothesis payload retains the question, theory, direction, and empirical basis", () => {
  assert.deepEqual(hypothesisPayload({
    researchQuestionId: "rq-1", statement: "  Effects differ  ", direction: "  positive ",
    theoreticalBasis: "  Theory  ", empiricalSupport: "  Prior studies  ",
  }), {
    research_question_id: "rq-1", statement: "Effects differ", direction: "positive",
    theoretical_basis: "Theory", empirical_support: "Prior studies",
  });
});
