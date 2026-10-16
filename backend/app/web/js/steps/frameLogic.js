// The currently navigable research screens. Later-stage entries in workflow.js are
// intentionally omitted until their screens are built.
const BUILT_JOURNEY = [
  { id: "overview", title: "Overview" },
  { id: "scope", title: "Scope" },
  { id: "criteria", title: "Criteria & seeds" },
  { id: "search", title: "Search" },
  { id: "results", title: "Results check" },
  { id: "sources", title: "Sources" },
  { id: "ask", title: "Ask" },
];

const STALE_STAGE_SCREENS = {
  search_planned: ["criteria", "search"],
  retrieved: ["results", "sources"],
  screened: ["screen"],
  extracted: ["evidence", "verification"],
  synthesized: ["ask"],
  drafted: ["write"],
};

export function previousStep(stepId) {
  const index = BUILT_JOURNEY.findIndex((step) => step.id === stepId);
  return index > 0 ? BUILT_JOURNEY[index - 1] : null;
}

export function stepHref(projectId, stepId) {
  return `#/p/${projectId}/${stepId}`;
}

export function staleForStep(stepId, artifacts = []) {
  const stages = Object.entries(STALE_STAGE_SCREENS)
    .filter(([, screens]) => screens.includes(stepId))
    .map(([stage]) => stage);
  return artifacts.filter((artifact) => stages.includes(artifact.stage));
}
