export const FEASIBILITY_FIELDS = [
  { item: "data_availability", label: "Data availability" },
  { item: "method_fit", label: "Method fit" },
  { item: "ethics_risk", label: "Ethics risk" },
  { item: "time_and_skills", label: "Time and skills" },
];

export const FEASIBILITY_STATUSES = ["ok", "concern", "blocker", "unsure"];

export function splitScopeContext(context = []) {
  const active = (context || []).filter((item) => item.retired_at == null && item.kind !== "idea");
  return {
    objectives: active.filter((item) => item.kind === "objective"),
    context: active.filter((item) => item.kind !== "objective"),
  };
}

export function feasibilityByItem(feasibility = []) {
  return Object.fromEntries((feasibility || []).map(({ item, status, note }) => [item, { status, note: note || "" }]));
}

export function ideaBriefPayload({ problem, whyItMatters, risks, selectedIdeaId, feasibility }) {
  return {
    problem: problem.trim(),
    why_it_matters: whyItMatters.trim(),
    risks: risks.trim() || null,
    selected_idea_id: selectedIdeaId || null,
    feasibility: FEASIBILITY_FIELDS.map(({ item }) => {
      const answer = feasibility[item] || {};
      return { item, status: answer.status || "unsure", note: (answer.note || "").trim() };
    }),
  };
}

export function landscapeScanPath(projectId, query) {
  const params = new URLSearchParams({ query: query.trim() });
  return `/api/projects/${projectId}/idea-brief/landscape-scan?${params}`;
}

export const GAP_TYPES = ["theoretical", "methodological", "empirical", "contextual", "population", "temporal"];

export function uniqueEvidenceIds(ids = []) {
  return [...new Set((ids || []).map((id) => String(id)))];
}

export function gapEvidenceReady(ids) {
  return uniqueEvidenceIds(ids).length >= 2;
}

export function gapPayload({ type, statement, supportingPaperIds, priority }) {
  return {
    type,
    statement: statement.trim(),
    supporting_paper_ids: uniqueEvidenceIds(supportingPaperIds),
    matrix_cell_ids: [],
    future_research: [],
    priority: Number(priority) || 0,
  };
}

export function selectedGapIds(gaps = []) {
  return (gaps || []).filter((gap) => gap.selected).map((gap) => String(gap.id));
}

export function researchQuestionPayload({ text, type, gapIds = [] }) {
  return { text: text.trim(), type: type.trim(), gap_ids: uniqueEvidenceIds(gapIds) };
}

export function finerStatusCounts(critique) {
  const items = critique.items || [];
  return {
    needsHumanReview: items.filter((item) => item.status === "needs_human_review").length,
    linkedToSelectedGaps: items.filter((item) => item.status === "linked_to_selected_gaps").length,
    missingGapLink: items.filter((item) => item.status === "missing_gap_link").length,
  };
}

export function hypothesisPayload({ researchQuestionId, statement, direction, theoreticalBasis, empiricalSupport }) {
  return {
    research_question_id: researchQuestionId,
    statement: statement.trim(),
    direction: direction.trim(),
    theoretical_basis: theoreticalBasis.trim(),
    empirical_support: empiricalSupport.trim(),
  };
}
