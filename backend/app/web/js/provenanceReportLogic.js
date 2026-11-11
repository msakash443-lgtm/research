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

// The report's own sections, in display order (X.31.24, owner answer W6, 2026-10-15: "the report includes only
// the sections the researcher chooses"). Ids match `app/provenance_report.py::REPORT_SECTIONS` and the API's
// `sections` request field / `included_sections` response field.
export const REPORT_SECTION_OPTIONS = [
  ["produced", "How the text was produced"],
  ["ai", "AI contributions"],
  ["pasted", "Pasted and quoted material"],
  ["statement", "AI-use statement"],
  ["limits", "What this report cannot show"],
];
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

// The per-section AI-use disclosure download in a chosen journal style (X.78). The style id comes from the server's
// list; it is encoded, never spliced in raw.
export function disclosureUrl(projectId, reportId, style) {
  if (!style) throw new Error("Choose a style first");
  return `/api/projects/${projectId}/provenance-reports/${reportId}/ai-disclosure?style=${encodeURIComponent(style)}&format=txt`;
}

export const DISCLOSURE_STALE = "This report is out of date, so a disclosure made from it could understate AI use. Make a new report first.";

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

// The "all sections" report defaults to covering every section (section_ids omitted, same request the server has
// always accepted). Unchecking a section in the picker scopes the next report to just what is still checked. All
// checked collapses back to "no filter" so an untouched picker never changes the request's shape.
export function sectionFilterPayload(allSectionIds, checkedIds) {
  const checked = new Set(checkedIds);
  if (allSectionIds.length === 0 || allSectionIds.every((id) => checked.has(id))) return null;
  return allSectionIds.filter((id) => checked.has(id));
}

// The POST body's `sections` for the report-section picker (X.31.24): every one of the five report sections
// checked (the untouched default) sends no filter, same "no filter" convention as `sectionFilterPayload`. Order
// matches `REPORT_SECTION_OPTIONS`, not whatever order the researcher (un)checked them in.
export function reportSectionsPayload(checkedIds) {
  const checked = new Set(checkedIds);
  const all = REPORT_SECTION_OPTIONS.map(([id]) => id);
  if (all.every((id) => checked.has(id))) return null;
  return all.filter((id) => checked.has(id));
}
