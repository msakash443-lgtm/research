// Conflicts & agreements panel of the Evidence table step (X.198). Pure: no DOM, no network. The server compares
// verified findings (app/contradictions.py); this only orders, labels and prepares a gap draft. It never decides
// who is right: "Make this a gap" only prefills a form a person must still read and save.

export const COMPARISON_KINDS = Object.freeze([
  { kind: "conflicts", label: "Conflict", tone: "bad" },
  { kind: "inconsistent", label: "Inconsistent (effect vs no effect)", tone: "warn" },
  { kind: "agreements", label: "Agreement", tone: "ok" },
]);

const DIRECTION_TEXT = { positive: "Effect: increase", negative: "Effect: decrease", mixed: "Effect: mixed" };

export function directionText(direction) {
  return DIRECTION_TEXT[direction] ?? "No effect reported";
}

// Groups of the chosen kinds in the fixed order conflicts, inconsistent, agreements.
// -> [{ kind, label, tone, exposure, outcome, populationNote, findings }]
export function comparisonGroups(result, kinds = COMPARISON_KINDS.map((k) => k.kind)) {
  const wanted = new Set(kinds);
  return COMPARISON_KINDS.filter((k) => wanted.has(k.kind)).flatMap((k) =>
    (result?.[k.kind] || []).map((group) => ({
      kind: k.kind, label: k.label, tone: k.tone, exposure: group.exposure, outcome: group.outcome,
      populationNote: group.population_note || null, findings: group.findings || [],
    }))
  );
}

export function skippedText(result) {
  const n = result?.unstructured_findings || 0;
  return n ? `${n} finding${n === 1 ? "" : "s"} skipped: extracted before the comparison fields existed` : "";
}

// The gap form's starting values for one group. Evidence is both papers' ids (distinct); the wording is a draft.
export function gapPrefill(group) {
  const seen = new Set();
  const papers = group.findings.filter((f) => !seen.has(f.source_id) && seen.add(f.source_id));
  const parts = papers.map((f) => `${f.title} (${directionText(f.direction).replace("Effect: ", "").toLowerCase()})`);
  const verb = group.kind === "agreements" ? "agree on" : "disagree on";
  return {
    type: "empirical",
    statement: `Studies ${verb} the effect of ${group.exposure} on ${group.outcome}: ${parts.join("; ")}.`,
    supportingPaperIds: papers.map((f) => String(f.source_id)),
  };
}

export const GAP_PREFILL_KEY = "gap-prefill";

export function saveGapPrefill(storage, projectId, prefill) {
  storage.setItem(`${GAP_PREFILL_KEY}:${projectId}`, JSON.stringify(prefill));
}

// Reads and clears the prefill: it is used once, and a corrupt value is ignored rather than breaking the gap form.
export function takeGapPrefill(storage, projectId) {
  const key = `${GAP_PREFILL_KEY}:${projectId}`;
  const raw = storage.getItem(key);
  storage.removeItem(key);
  if (!raw) return null;
  try {
    const value = JSON.parse(raw);
    return value && Array.isArray(value.supportingPaperIds) && typeof value.statement === "string" ? value : null;
  } catch {
    return null;
  }
}
