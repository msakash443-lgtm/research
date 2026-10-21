// The writing provenance report panel (X.31.12.2, design S5). Lists this project's reports, makes new ones, and shows
// each report's numbers, AI and pasted items, the AI-use statement, and what the report cannot show. Nothing is sent to
// a model: the server computes every number and the statement. Reports are append-only, so an older report stays as it
// was, and a covered section saved again marks it "Out of date".
import { el, formatDate, toast } from "./dom.js";
import { projectApi, request } from "./api.js";
import { errorCard } from "./steps/common.js";
import { aiLines, coverageLine, disclosureUrl, DISCLOSURE_STALE, exportUrl, pastedLines, reportSectionsPayload, REPORT_SECTION_OPTIONS, sectionFilterPayload, shareRows, staleNotice } from "./provenanceReportLogic.js";

const INTRO = "A record of how the saved text was produced: typed, quoted, pasted or AI-edited and accepted. It counts characters from saved versions only.";

export async function renderReport(host, { projectId, section }) {
  host.replaceChildren(el("p", { className: "muted small", text: "Loading reports…" }));
  let reports, sections, styles;
  try {
    [reports, sections, styles] = await Promise.all([
      request(projectApi(projectId, "/provenance-reports")),
      request(projectApi(projectId, "/documents")),
      request(projectApi(projectId, "/ai-disclosure-styles")),
    ]);
  } catch (error) {
    host.replaceChildren(errorCard(error.message, () => renderReport(host, { projectId, section })));
    return;
  }

  const sectionIds = sections.map((doc) => doc.id);
  const checked = new Set(sectionIds);
  // Report sections (X.31.24, owner answer W6): which of the report's own sections ("How produced", "AI
  // contributions", "Pasted and quoted material", "AI-use statement", "What this cannot show") to include.
  // All checked by default, same "no filter" convention as the document-section picker above.
  const reportSectionsChecked = new Set(REPORT_SECTION_OPTIONS.map(([id]) => id));

  const make = (scope, documentId) => async () => {
    const filter = scope === "manuscript" ? sectionFilterPayload(sectionIds, checked) : null;
    if (scope === "manuscript" && filter !== null && filter.length === 0) {
      toast("Choose at least one section first.");
      return;
    }
    const sectionsFilter = reportSectionsPayload(reportSectionsChecked);
    if (sectionsFilter !== null && sectionsFilter.length === 0) {
      toast("Choose at least one report section first.");
      return;
    }
    const body = { scope };
    if (documentId) body.document_id = documentId;
    if (filter) body.section_ids = filter;
    if (sectionsFilter) body.sections = sectionsFilter;
    try {
      await request(projectApi(projectId, "/provenance-reports"), { method: "POST", body: JSON.stringify(body) });
      toast("Report made");
    } catch (error) {
      toast(error.message);
      return;
    }
    renderReport(host, { projectId, section });
  };

  const sectionButton = el("button", { type: "button", text: "Make report for this section", attrs: section ? {} : { disabled: "disabled", title: "Open a section first" } });
  sectionButton.addEventListener("click", make("section", section?.id));
  const manuscriptButton = el("button", { type: "button", className: "quiet-button", text: "Make report for selected sections" });
  manuscriptButton.addEventListener("click", make("manuscript", null));

  const sectionPicker = sections.length
    ? el("fieldset", { className: "write-report-sections", children: [
        el("legend", { text: "Sections to include (all-sections report)" }),
        ...sections.map((doc) => {
          const box = el("input", { attrs: { type: "checkbox" } });
          box.checked = true;
          box.addEventListener("change", () => {
            if (box.checked) checked.add(doc.id);
            else checked.delete(doc.id);
          });
          return el("label", { className: "write-report-section-pick", children: [box, el("span", { text: doc.title })] });
        }),
      ] })
    : null;

  const reportSectionPicker = el("fieldset", { className: "write-report-sections", children: [
    el("legend", { text: "Report sections to include" }),
    ...REPORT_SECTION_OPTIONS.map(([id, label]) => {
      const box = el("input", { attrs: { type: "checkbox" } });
      box.checked = true;
      box.addEventListener("change", () => {
        if (box.checked) reportSectionsChecked.add(id);
        else reportSectionsChecked.delete(id);
      });
      return el("label", { className: "write-report-section-pick", children: [box, el("span", { text: label })] });
    }),
  ] });

  host.replaceChildren(
    el("h3", { text: "Writing record" }),
    el("p", { className: "small", text: INTRO }),
    ...(sectionPicker ? [sectionPicker] : []),
    reportSectionPicker,
    el("div", { className: "write-report-actions", children: [sectionButton, manuscriptButton] }),
    ...(reports.length === 0
      ? [el("p", { className: "muted small", text: "No reports yet. Save a section, then make a report." })]
      : reports.map((report) => reportCard(report, projectId, styles))),
  );
}

function reportCard(report, projectId, styles) {
  const notice = staleNotice(report);
  const included = new Set(report.included_sections);
  const producedBlocks = included.has("produced")
    ? [
        el("p", { className: "muted small", text: `${report.report.total_chars} characters counted · ${report.report.sessions.count} writing sessions` }),
        el("ul", { className: "write-report-shares", children: shareRows(report.report.shares).map((row) =>
          el("li", { children: [el("span", { text: row.label }), el("span", { className: "write-report-pct", text: row.pct })] })
        ) }),
      ]
    : [el("p", { className: "muted small", text: `${report.report.total_chars} characters counted` })];
  const aiBlocks = included.has("ai")
    ? (() => {
        const aiItems = aiLines(report.report.ai.items);
        const aiList = aiItems.length
          ? el("ul", { className: "small", children: aiItems.map((item) => el("li", { text: `${item.label} · ${item.model} · ${item.prompt} · ${item.kept}` })) })
          : el("p", { className: "muted small", text: "No AI-edited text recorded." });
        return [el("h4", { text: "AI contributions" }), aiList];
      })()
    : [];
  const pastedBlocks = included.has("pasted")
    ? (() => {
        const pasted = pastedLines(report.report.pasted);
        const pastedList = pasted.length
          ? el("ul", { className: "small", children: pasted.map((item) => el("li", { text: `${item.status} · ${item.chars} · ${item.source}` })) })
          : el("p", { className: "muted small", text: "No pasted or quoted material recorded." });
        return [el("h4", { text: "Pasted and quoted material" }), pastedList];
      })()
    : [];
  const statementBlocks = included.has("statement")
    ? (() => {
        const copy = el("button", { type: "button", className: "quiet-button", text: "Copy statement" });
        copy.addEventListener("click", async () => {
          try {
            await navigator.clipboard.writeText(report.statement_text);
            toast("Statement copied");
          } catch {
            toast("Could not copy. Select the text and copy it by hand.");
          }
        });
        return [el("h4", { text: "AI-use statement" }), el("p", { className: "small write-report-statement", text: report.statement_text }), copy];
      })()
    : [];
  const limitsBlocks = included.has("limits")
    ? [el("h4", { text: "What this record cannot show" }), el("ul", { className: "muted small", children: report.report.limits.map((limit) => el("li", { text: limit })) })]
    : [];

  // Per-section AI-use disclosure in a journal style (X.78): the server refuses a stale report, so say why here.
  const disclosure = (() => {
    if (notice) return el("p", { className: "muted small", text: DISCLOSURE_STALE });
    const select = el("select", { attrs: { "aria-label": "Disclosure wording" } });
    styles.forEach((style) => select.append(el("option", { text: style.label, attrs: { value: style.id } })));
    const link = el("a", { text: "Download AI-use disclosure", href: disclosureUrl(projectId, report.id, select.value), attrs: { download: "" } });
    select.addEventListener("change", () => link.setAttribute("href", disclosureUrl(projectId, report.id, select.value)));
    return el("div", { className: "write-report-disclosure", children: [
      el("h4", { text: "AI-use disclosure by section" }),
      el("p", { className: "muted small", text: "One line per section, in a journal-style wording. A template: check the journal's current policy." }),
      select, document.createTextNode(" "), link,
    ] });
  })();

  return el("section", { className: "write-report", attrs: { "aria-label": `Report made ${formatDate(report.created_at)}` }, children: [
    el("header", { className: "write-report-head", children: [
      el("strong", { text: `Report made ${formatDate(report.created_at)}` }),
      ...(notice ? [el("span", { className: "write-report-stale", text: notice.badge, attrs: { title: notice.detail } })] : []),
    ] }),
    ...(notice ? [el("p", { className: "small", text: notice.detail })] : []),
    el("p", { className: "small", text: coverageLine(report.covered_versions) }),
    ...producedBlocks,
    ...aiBlocks,
    ...pastedBlocks,
    ...statementBlocks,
    ...limitsBlocks,
    disclosure,
    el("div", { className: "write-report-export", children: [
      el("a", { text: "Download Markdown", href: exportUrl(projectId, report.id, "md"), attrs: { download: "" } }),
      el("a", { text: "Download JSON", href: exportUrl(projectId, report.id, "json"), attrs: { download: "" } }),
    ] }),
  ] });
}
