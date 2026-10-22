// Pure logic for the Scope screen's research-type picker (X.43.9). No DOM, no fetch.
// The server is the authority (PUT /api/projects/{id}/research-type refuses after idea or after a gate is
// approved); this only decides what the picker shows, so the owner is not offered a change that would be refused.

export function canChangeResearchType({ isOwner, stage, gates = [] }) {
  if (!isOwner) return false;
  if (stage !== "idea") return false;
  return !gates.some((gate) => gate.status === "approved");
}

// Why the picker is locked for this viewer (wireframe Q3, owner answer 2026-10-22: other types stay visible,
// greyed out, with the reason). Null when the type can be changed.
export function researchTypeLockReason({ isOwner, stage, gates = [] }) {
  if (canChangeResearchType({ isOwner, stage, gates })) return null;
  if (stage !== "idea") return "Locked: the project has moved past Idea, so the research type can no longer change.";
  const approved = gates.find((gate) => gate.status === "approved");
  if (approved) return `Locked: gate ${approved.code} is approved, so the research type can no longer change.`;
  return "Only the owner can change the research type.";
}

// Which research type a radio group should show as chosen: the picked one while the owner is choosing,
// otherwise the saved one.
export function selectedResearchType({ saved, picked }) {
  return picked || saved;
}

// Whether "Save" should be enabled: only a real change to a changeable project.
export function canSaveResearchType({ saved, picked, changeable }) {
  return changeable && Boolean(picked) && picked !== saved;
}

// The stage list for one type, in the server's order, with the gate that completes each stage and whether that
// gate is mandatory in every type (decision 5). `chain` is the GET /stage shape: [{stage, gate, label}].
export const MANDATORY_GATES = ["G2", "G3", "G4", "G7", "G11"];
export function chainRows(chain) {
  return (chain || []).map((item, index) => ({
    position: index + 1,
    stage: item.stage,
    label: item.label || null,
    gate: item.gate || null,
    mandatory: Boolean(item.gate) && MANDATORY_GATES.includes(item.gate),
  }));
}
