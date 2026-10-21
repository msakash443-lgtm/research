import { el, emptyCard, toast } from "../dom.js";
import { request, projectApi } from "../api.js";
import { provenanceBadge } from "../provenance.js";
import { coverageBadge } from "./common.js";
import { PAPERS_AS_COLUMNS, PAPERS_AS_ROWS, normalizeOrientation, tableLayout } from "../evidenceTableLogic.js";

function display(value) {
  if (value === null || value === undefined) return "Not reported";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

function sourcePanel(panel, data) {
  panel.replaceChildren(el("h2", { text: data.title }),
    el("p", { className: "muted small", text: `Page ${data.page.join(", ")}${data.section ? ` · ${data.section}` : ""} · PDF ${data.sha256.slice(0, 12)}…` }));
  const passage = el("p", { className: "evidence-passage" });
  passage.append(document.createTextNode(data.text.slice(0, data.highlight_start)));
  passage.append(el("mark", { text: data.text.slice(data.highlight_start, data.highlight_end) }));
  passage.append(document.createTextNode(data.text.slice(data.highlight_end)));
  panel.append(passage);
}

async function render(root, ctx) {
  const base = projectApi(ctx.project.id, "/evidence-matrix");
  const schemas = await request(projectApi(ctx.project.id, "/extraction-schemas"));
  const selected = root.dataset.schemaName || "default";
  const orientation = normalizeOrientation(root.dataset.orientation);
  const matrix = await request(`${base}?schema_name=${encodeURIComponent(selected)}`);
  const heading = el("div", { className: "panel" });
  heading.append(el("h2", { text: "Evidence table" }),
    el("p", { className: "muted", text: "One extraction per paper for the selected schema version. Checked means a person verified it; matched means the quote was checked against the stored PDF but awaits human review." }));
  const controls = el("div", { className: "evidence-controls" });
  const select = el("select", { attrs: { "aria-label": "Extraction schema" } });
  [...schemas.built_in, ...schemas.project].forEach((schema) => {
    const option = el("option", { text: `${schema.label} (${schema.schema_version})`, attrs: { value: schema.ref } });
    option.selected = schema.ref === selected;
    select.append(option);
  });
  select.addEventListener("change", () => { root.dataset.schemaName = select.value; render(root, ctx).catch((error) => toast(error.message)); });
  controls.append(select);
  const flip = el("button", { className: "quiet-button", text: orientation === PAPERS_AS_ROWS ? "Show papers as columns" : "Show papers as rows" });
  flip.addEventListener("click", () => {
    root.dataset.orientation = orientation === PAPERS_AS_ROWS ? PAPERS_AS_COLUMNS : PAPERS_AS_ROWS;
    render(root, ctx).catch((error) => toast(error.message));
  });
  controls.append(flip);
  for (const [extension, label] of [["csv", "Download CSV"], ["xlsx", "Download Excel"]]) {
    controls.append(el("a", { text: label, className: "button quiet-button",
      href: `${base}/export.${extension}?schema_name=${encodeURIComponent(selected)}` }));
  }
  heading.append(controls);
  root.replaceChildren(heading);
  if (!matrix.papers.length) {
    root.append(emptyCard("No extracted papers yet", "Add extractions for this schema, then return to compare their fields."));
    return;
  }
  const workspace = el("div", { className: "evidence-workspace" });
  const scroll = el("div", { className: "evidence-scroll" });
  const table = el("table", { className: "versions-table evidence-table" });
  const layout = tableLayout(matrix, orientation);
  const headerLabel = (item) => item.field
    ? el("th", { text: `${item.field.key}${item.field.critical ? " *" : ""}`, attrs: { title: item.field.description } })
    : el("th", { text: item.paper.title });
  const header = el("tr");
  header.append(el("th", { text: orientation === PAPERS_AS_COLUMNS ? "Field" : "Paper" }));
  layout.headers.forEach((item) => header.append(headerLabel(item)));
  table.append(header);
  const panel = el("aside", { className: "panel evidence-source", attrs: { "aria-live": "polite" } });
  panel.append(el("h2", { text: "Source text" }), el("p", { text: "Select a populated cell to inspect its quote in the stored PDF text." }));
  const paperHead = (paper) => {
    const title = el("td");
    title.append(el("strong", { text: paper.title }), coverageBadge(paper.coverage), el("p", { className: "muted small",
      text: !paper.extraction_id ? "Not extracted" : paper.verified_by_human ? "Human verified" : "Needs human review" }));
    if (paper.extracted_by === "ai") title.append(provenanceBadge({ producer: "AI extraction", model: paper.model_id,
      promptVersion: paper.prompt_version, date: paper.created_at,
      approval: { text: paper.verified_by_human ? "Verified by person" : "Not verified", state: paper.verified_by_human ? "approved" : "pending" } }));
    return title;
  };
  for (const item of layout.rows) {
    const row = el("tr");
    if (item.head.paper) row.append(paperHead(item.head.paper));
    else {
      const head = el("td");
      head.append(el("strong", { text: `${item.head.field.key}${item.head.field.critical ? " *" : ""}` }),
        el("p", { className: "muted small", text: item.head.field.description }));
      row.append(head);
    }
    for (const { cell } of item.cells) {
      const td = el("td");
      if (cell.source_text_url) {
        const button = el("button", { className: "evidence-cell", text: display(cell.value) });
        button.append(el("small", { text: cell.status === "checked" ? "Checked" : "Matched · review needed" }));
        button.addEventListener("click", async () => {
          panel.replaceChildren(el("p", { text: "Loading source text…" }));
          try { sourcePanel(panel, await request(cell.source_text_url)); }
          catch (error) { panel.replaceChildren(el("p", { text: error.message })); }
        });
        td.append(button);
      } else td.append(el("span", { className: "muted", text: cell.status === "not_extracted" ? "Not extracted" : "Not reported" }));
      const origin = el("p", { className: "muted small" });
      origin.append(cell.original_url
        ? el("a", { text: "Original paper", attrs: { href: cell.original_url, target: "_blank", rel: "noopener noreferrer" } })
        : document.createTextNode("No DOI or URL on record"));
      td.append(origin);
      row.append(td);
    }
    table.append(row);
  }
  scroll.append(table);
  workspace.append(scroll, panel);
  root.append(workspace, el("p", { className: "muted small", text: "* Critical field. Export includes values, review status, quotes, pages and sections." }));
}

export default { render };
