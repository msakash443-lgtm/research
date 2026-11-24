// Deterministic local guidance for the stage-help panel. It consumes the project summary
// already loaded by the workspace; it never fetches, persists a conversation, or calls a model.
import { label } from "./dom.js";

const STAGE_HELP = {
  idea: "Define the problem, intended contribution, and an initial research question before asking for scope approval.",
  scoped: "Record the research question, objectives, and key decisions. A human must decide the scope gate before later work proceeds.",
  search_planned: "Set clear inclusion criteria, seed papers, and a reproducible search strategy before requesting search-plan approval.",
  retrieved: "Review retrieved sources, resolve duplicates, and screen each record against the agreed criteria.",
  screened: "Review the included set and its reasons. The screening gate is a human decision; this helper cannot approve it.",
  extracted: "Store the relevant full text, extract only evidence backed by it, and have a person verify critical fields.",
  synthesized: "Compare verified evidence, identify supported patterns and gaps, and keep claims tied to their sources.",
  drafted: "Draft the report with traceable evidence and authorship records, then request the required human review.",
};

export function stageAssistantState(summary, stages) {
  const currentId = summary.stage?.stage;
  const chain = summary.stage?.chain || stages || [];
  const current = chain.find((item) => item.stage === currentId) || { stage: currentId, label: null };
  const next = (summary.stage?.next || []).find((item) => item.ready) || (summary.stage?.next || [])[0] || null;
  const gate = next?.gate ? (summary.gates || []).find((item) => item.code === next.gate) : null;
  return {
    current: { id: currentId, label: current?.label || label(currentId || "research project") },
    stages: chain.map((item) => ({ id: item.stage, label: item.label || label(item.stage), current: item.stage === currentId })),
    next: next ? { label: next.label || label(next.stage), gate: next.gate, ready: next.ready, gateStatus: gate?.status } : null,
  };
}

export function answerStageQuestion(question, state) {
  const text = question.trim().toLowerCase();
  if (!text) return "Ask about the current stage, what comes next, or a stage such as screening, evidence, or writing.";
  if (text.includes("method") || text.includes("design")) return "For methodology, define the design, participants or materials, collection procedure, and analysis plan. Record decisions before asking the authorized person to approve the relevant gate.";
  if (text.includes("next") || text.includes("proceed") || text.includes("block")) {
    if (!state.next) return `Complete the work for ${state.current.label}, then review the project path for the next required decision.`;
    const gate = state.next.gate ? ` ${state.next.gate} remains a human decision${state.next.gateStatus ? ` (currently ${state.next.gateStatus})` : ""}.` : "";
    return `Next: ${state.next.label}.${gate} ${state.next.ready ? "Its listed prerequisites are currently met; use Approvals when you are ready for a person to decide." : "Review the current stage requirements before moving on."}`;
  }
  return `${state.current.label}: ${STAGE_HELP[state.current.id] || "Review the stage requirements, keep evidence and decisions traceable, and use the project path to find the next safe action."}`;
}
