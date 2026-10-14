// Pure helpers for the writing provenance report panel (X.31.12.2). No DOM here, so Node can test them.
// The numbers and the statement come from the server; this file only chooses labels and formats text.

const SHARE_ORDER = [
  ["typed", "Typed by authors"],
  ["quoted", "Quoted (with citation)"],
  ["pasted_own", "Pasted, confirmed own"],
  ["pasted_unresolved", "Pasted, unresolved"],
  ["ai", "AI-edited, accepted"],
];
const ACTION_LABELS = {
  challenge: "Challenge my argument",
  find_evidence: "Find evidence",
  clearer_sentence: "Clearer sentence",
  argument_check: "Argument check",
};
const PASTE_LABELS = { unconfirmed: "Unconfirmed paste", own: "Own words", quotation: "Quotation" };
const EXPORT_FORMATS = ["md", "json"];
const STALE_DETAIL = "A covered section changed after this report was made. Make a new report to include the change.";

export function shareRows(shares) {
  return SHARE_ORDER.map(([key, label]) => ({ key, label, pct: `${Number(shares?.[key] ?? 0).toFixed(1)}%` }));
}

export function coverageLine(covered) {
  const count = covered.length;
  const sections = covered.map((section) => `${section.title} (version ${section.number})`).join(", ");
  return `Covers ${count} ${count === 1 ? "section" : "sections"}: ${sections}`;
}

export function staleNotice(report) {
  if (!report.stale) return null;
  return { badge: "Out of date", detail: STALE_DETAIL };
}

export function exportUrl(projectId, reportId, format) {
  if (!EXPORT_FORMATS.includes(format)) throw new Error(`Unknown export format: ${format}`);
  return `/api/projects/${projectId}/provenance-reports/${reportId}?format=${format}`;
}

export function aiLines(items) {
  return items.map((item) => ({
    label: ACTION_LABELS[item.action] ?? item.action,
    model: item.model_id || "not recorded",
    prompt: item.prompt_version,
    kept: `${item.chars_kept} characters kept`,
  }));
}

export function pastedLines(items) {
  return items.map((item) => ({
    status: PASTE_LABELS[item.status] ?? item.status,
    chars: `${item.chars} characters`,
    source: item.source_id ? `source ${item.source_id}` : "no source linked",
  }));
}
