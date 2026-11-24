// Team step: CRediT contributions and the responsibility matrix (X.226). Pure: no DOM, no network.
// Three separate things: a project role authorises (what you may do in the app), a CRediT contribution reports
// who contributed what to the manuscript, and a responsibility says who leads or supports an area of the work now.
// None of them is derived from another, and only the project role is ever checked to allow or refuse an action.

// mirrors app/credit.py CREDIT_ROLES (the 14 standard CRediT terms, in the taxonomy's own order); the server re-checks
export const CREDIT_ROLES = Object.freeze([
  "Conceptualization", "Data curation", "Formal analysis", "Funding acquisition", "Investigation", "Methodology",
  "Project administration", "Resources", "Software", "Supervision", "Validation", "Visualization",
  "Writing - original draft", "Writing - review & editing",
]);

// The role list after ticking or unticking one role: taxonomy order, no duplicates, input unchanged.
export function creditSelection(roles, role, checked) {
  const chosen = new Set(roles || []);
  if (checked) chosen.add(role);
  else chosen.delete(role);
  return CREDIT_ROLES.filter((name) => chosen.has(name));
}

// One row per area: { code, label, lead: userId | null, support: [userId] } from the server's matrix.
export function matrixRows(matrix) {
  return (matrix?.areas || []).map(({ code, label }) => {
    const here = (matrix.assignments || []).filter((a) => a.area === code);
    return {
      code, label,
      lead: here.find((a) => a.level === "lead")?.user_id ?? null,
      support: here.filter((a) => a.level === "support").map((a) => a.user_id),
    };
  });
}

export function levelOf(row, userId) {
  if (row.lead === userId) return "lead";
  return row.support.includes(userId) ? "support" : "";
}

// The full assignment list for one area after `userId` is set to `level` ("lead", "support" or "" for none).
// A new lead replaces the old one, who stays on as support rather than silently dropping off.
export function assignmentsAfter(row, userId, level) {
  let lead = row.lead;
  const support = new Set(row.support);
  if (lead === userId) lead = null;
  support.delete(userId);
  if (level === "lead") {
    if (lead) support.add(lead);
    lead = userId;
  } else if (level === "support") {
    support.add(userId);
  }
  return [...(lead ? [{ user_id: lead, level: "lead" }] : []), ...[...support].map((id) => ({ user_id: id, level: "support" }))];
}
