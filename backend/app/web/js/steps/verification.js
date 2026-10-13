import { el, emptyCard, toast } from "../dom.js";
import { request, projectApi } from "../api.js";
import { provenanceBadge } from "../provenance.js";

function show(value) {
  if (value === null || value === undefined) return "Not reported in this paper";
  return typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
}

async function renderReview(root, ctx, item) {
  const base = projectApi(ctx.project.id, `/extractions/${item.extraction_id}`);
  const extraction = await request(base);
  const schemaName = extraction.schema_version.slice(0, extraction.schema_version.lastIndexOf("@"));
  const schemaPath = schemaName.startsWith("project:") ? schemaName.slice("project:".length) : schemaName;
  const schema = await request(projectApi(ctx.project.id, `/extraction-schemas/${encodeURIComponent(schemaPath)}`));
  const work = el("div", { className: "evidence-workspace" });
  const documentPane = el("section", { className: "panel" });
  documentPane.append(el("h2", { text: `Stored PDF · ${item.title}` }),
    el("p", { className: "muted small", text: "Use the original PDF to check each value and its quoted page or section." }));
  documentPane.append(el("iframe", { className: "verification-pdf", attrs: {
    src: `${base}/pdf`, title: `Stored PDF for ${item.title}`, loading: "lazy"
  } }));
  const fieldsPane = el("section", { className: "panel" });
  fieldsPane.append(el("h2", { text: "Extraction review" }),
    el("p", { className: "muted small", text: `${extraction.schema_version} · ${extraction.verified_by_human ? "Verified by a person" : "Awaiting human verification"}` }));
  if (extraction.extracted_by === "ai") fieldsPane.append(provenanceBadge({
    producer: "AI extraction", model: extraction.model_id, promptVersion: extraction.prompt_version,
    date: extraction.created_at, approval: { text: extraction.verified_by_human ? "Verified by person" : "Pending", state: extraction.verified_by_human ? "approved" : "pending" }
  }));
  const checks = [];
  for (const field of schema.fields) {
    const card = el("div", { className: "verification-field" });
    card.append(el("h3", { text: `${field.key}${field.critical ? " *" : ""}` }),
      el("p", { className: "muted small", text: field.description }),
      el("pre", { text: show(extraction.fields_json[field.key]) }));
    const span = extraction.evidence_spans[field.key];
    if (span) {
      card.append(el("blockquote", { text: span.quote }), el("p", { className: "muted small",
        text: `Page ${span.page ?? "—"}${span.section ? ` · ${span.section}` : ""}` }));
    }
    if (field.critical && !extraction.verified_by_human) {
      const label = el("label", { className: "verification-check" });
      const input = el("input", { type: "checkbox", attrs: { value: field.key } });
      label.append(input, document.createTextNode(` I checked ${field.key} against the PDF${span ? "" : " and confirmed it is not reported"}`));
      card.append(label);
      checks.push(input);
    }
    fieldsPane.append(card);
  }
  if (!extraction.verified_by_human && (ctx.canWrite || ctx.role === "supervisor")) {
    const verify = el("button", { text: "Verify critical fields" });
    verify.disabled = checks.length > 0;
    checks.forEach((check) => check.addEventListener("change", () => { verify.disabled = !checks.every((input) => input.checked); }));
    verify.addEventListener("click", async () => {
      verify.disabled = true;
      try {
        await request(`${base}/verify`, { method: "POST", body: JSON.stringify({ confirmed_critical_fields: checks.map((input) => input.value) }) });
        toast("Extraction verified by you.");
        await render(root, ctx);
      } catch (error) { toast(error.message); verify.disabled = false; }
    });
    fieldsPane.append(verify);
  }
  work.append(documentPane, fieldsPane);
  root.append(work);
}

async function render(root, ctx) {
  const items = await request(projectApi(ctx.project.id, "/extractions/verification-queue"));
  const heading = el("div", { className: "panel" });
  heading.append(el("h2", { text: "Verify extraction" }),
    el("p", { text: "Check critical values, including sample size, measures and reported effects, against the stored PDF before G4 approval." }));
  root.replaceChildren(heading);
  if (!items.length) {
    root.append(emptyCard("No papers to verify", "Add included sources and their extractions before G4 approval."));
    return;
  }
  const select = el("select", { attrs: { "aria-label": "Paper to verify" } });
  for (const item of items) {
    const state = item.schema_stale ? "Old schema" : item.extraction_id ? item.verified_by_human ? "Verified" : "Needs review" : "Not extracted";
    select.append(el("option", { text: `${item.title} · ${state}`, attrs: { value: item.source_id } }));
  }
  const requested = root.dataset.sourceId;
  if (requested && items.some((item) => item.source_id === requested)) select.value = requested;
  const active = items.find((item) => item.source_id === select.value) || items[0];
  const picker = el("div", { className: "panel" });
  picker.append(el("label", { text: "Paper " }), select);
  root.append(picker);
  select.addEventListener("change", () => { root.dataset.sourceId = select.value; render(root, ctx).catch((error) => toast(error.message)); });
  if (active.schema_stale) root.append(emptyCard("Schema changed", "Create a new extraction with the current schema before verification."));
  else if (!active.extraction_id) root.append(emptyCard("No extraction", "Extract this paper before verification."));
  else await renderReview(root, ctx, active);
}

export default { render };
