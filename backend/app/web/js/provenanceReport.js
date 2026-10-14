// The writing provenance report panel (X.31.12.2, design S5). Lists this project's reports, makes new ones, and shows
// each report's numbers, AI and pasted items, the AI-use statement, and what the report cannot show. Nothing is sent to
// a model: the server computes every number and the statement. Reports are append-only, so an older report stays as it
// was, and a covered section saved again marks it "Out of date".
import { el, formatDate, toast } from "./dom.js";
import { projectApi, request } from "./api.js";
import { errorCard } from "./steps/common.js";
import { aiLines, coverageLine, exportUrl, pastedLines, shareRows, staleNotice } from "./provenanceReportLogic.js";

const INTRO = "A record of how the saved text was produced: typed, quoted, pasted or AI-edited and accepted. It counts characters from saved versions only.";

export async function renderReport(host, { projectId, section }) {
  host.replaceChildren(el("p", { className: "muted small", text: "Loading reports…" }));
  let reports;
  try {
    reports = await request(projectApi(projectId, "/provenance-reports"));
  } catch (error) {
    host.replaceChildren(errorCard(error.message, () => renderReport(host, { projectId, section })));
    return;
  }

  const make = (scope, documentId) => async () => {
    try {
      await request(projectApi(projectId, "/provenance-reports"), {
        method: "POST",
        body: JSON.stringify(documentId ? { scope, document_id: documentId } : { scope }),
      });
      toast("Report made");
    } catch (error) {
      toast(error.message);
      return;
    }
    renderReport(host, { projectId, section });
  };

  const sectionButton = el("button", { type: "button", text: "Make report for this section", attrs: section ? {} : { disabled: "disabled", title: "Open a section first" } });
  sectionButton.addEventListener("click", make("section", section?.id));
  const manuscriptButton = el("button", { type: "button", className: "quiet-button", text: "Make report for all sections" });
  manuscriptButton.addEventListener("click", make("manuscript", null));

  host.replaceChildren(
    el("h3", { text: "Writing record" }),
    el("p", { className: "small", text: INTRO }),
    el("div", { className: "write-report-actions", children: [sectionButton, manuscriptButton] }),
    ...(reports.length === 0
      ? [el("p", { className: "muted small", text: "No reports yet. Save a section, then make a report." })]
      : reports.map((report) => reportCard(report, projectId))),
  );
}

function reportCard(report, projectId) {
  const notice = staleNotice(report);
  const shares = el("ul", { className: "write-report-shares", children: shareRows(report.report.shares).map((row) =>
    el("li", { children: [el("span", { text: row.label }), el("span", { className: "write-report-pct", text: row.pct })] })
  ) });
  const aiItems = aiLines(report.report.ai.items);
  const aiList = aiItems.length
    ? el("ul", { className: "small", children: aiItems.map((item) => el("li", { text: `${item.label} · ${item.model} · ${item.prompt} · ${item.kept}` })) })
    : el("p", { className: "muted small", text: "No AI-edited text recorded." });
  const pasted = pastedLines(report.report.pasted);
  const pastedList = pasted.length
    ? el("ul", { className: "small", children: pasted.map((item) => el("li", { text: `${item.status} · ${item.chars} · ${item.source}` })) })
    : el("p", { className: "muted small", text: "No pasted or quoted material recorded." });
  const copy = el("button", { type: "button", className: "quiet-button", text: "Copy statement" });
  copy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(report.statement_text);
      toast("Statement copied");
    } catch {
      toast("Could not copy. Select the text and copy it by hand.");
    }
  });

  return el("section", { className: "write-report", attrs: { "aria-label": `Report made ${formatDate(report.created_at)}` }, children: [
    el("header", { className: "write-report-head", children: [
      el("strong", { text: `Report made ${formatDate(report.created_at)}` }),
      ...(notice ? [el("span", { className: "write-report-stale", text: notice.badge, attrs: { title: notice.detail } })] : []),
    ] }),
    ...(notice ? [el("p", { className: "small", text: notice.detail })] : []),
    el("p", { className: "small", text: coverageLine(report.covered_versions) }),
    el("p", { className: "muted small", text: `${report.report.total_chars} characters counted · ${report.report.sessions.count} writing sessions` }),
    shares,
    el("h4", { text: "AI contributions" }),
    aiList,
    el("h4", { text: "Pasted and quoted material" }),
    pastedList,
    el("h4", { text: "AI-use statement" }),
    el("p", { className: "small write-report-statement", text: report.statement_text }),
    copy,
    el("h4", { text: "What this record cannot show" }),
    el("ul", { className: "muted small", children: report.report.limits.map((limit) => el("li", { text: limit })) }),
    el("div", { className: "write-report-export", children: [
      el("a", { text: "Download Markdown", href: exportUrl(projectId, report.id, "md"), attrs: { download: "" } }),
      el("a", { text: "Download JSON", href: exportUrl(projectId, report.id, "json"), attrs: { download: "" } }),
    ] }),
  ] });
}
