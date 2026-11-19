// Which gate the Approvals page puts first (X.218). Pure: no DOM, no network. This only orders and groups what the
// server sent; it never changes whether a gate can be approved (that is `gate.can_decide`, decided server-side).

// The one gate to act on now: the current stage's own gate while it is not approved; otherwise the first gate the
// caller can decide, then the first gate not approved. null when every gate is approved.
export function focusGate(gates, stage, stages) {
  const open = (gates || []).filter((g) => g.status !== "approved");
  if (open.length === 0) return null;
  const entry = (stages || []).find(([name]) => name === stage);
  const own = entry?.[1] ? open.find((g) => g.code === entry[1]) : null;
  return own || open.find((g) => g.can_decide) || open[0];
}

// -> { focus, attention, done, later }: `attention` = other rejected gates (never collapsed away), `done` = approved,
// `later` = the remaining pending gates. Server order is kept inside each group.
export function gateGroups(gates, stage, stages) {
  const focus = focusGate(gates, stage, stages);
  const rest = (gates || []).filter((g) => g !== focus);
  return {
    focus,
    attention: rest.filter((g) => g.status === "rejected"),
    done: rest.filter((g) => g.status === "approved"),
    later: rest.filter((g) => g.status !== "approved" && g.status !== "rejected"),
  };
}

// One sentence on why the focus gate is or is not yours to decide now.
export function focusStatus(gate) {
  if (!gate) return "Every gate is approved.";
  if (gate.can_decide) return "Waiting for your decision.";
  const waiting = gate.waiting_for || [];
  return waiting.length ? `Not open yet: waits for ${waiting.join(", ")}.` : "Waiting for someone with approval rights.";
}

// "6 of 19 stages done" and the names to show next to it, from the stage chain and the current stage.
export function stageProgress(stages, current) {
  const index = (stages || []).findIndex(([name]) => name === current);
  return { done: Math.max(index, 0), total: (stages || []).length, currentIndex: index };
}
