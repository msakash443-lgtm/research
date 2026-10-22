import { $, el, formatDate, toast } from "../dom.js";
import { request, projectApi } from "../api.js";
import { AI_LEVELS, AI_ROUTES, aiLevel } from "../aiLevels.js";
import { usageSummary } from "../usageLogic.js";
import { callRows, durationRows, failureRows, rememberRun, stepRows, tiles } from "../observabilityLogic.js";
import { extractionNotes, extractionTables, hasAnything, screeningLines } from "../accuracyLogic.js";
import { FOCUS_RESEARCH_RUN_KEY } from "./ask.js";

let offset = 0;
let actionFilter = "";
let rows = [];
let currentProjectId = null;

function actorName(ctx, actor) {
  const member = (ctx.summary.members || []).find((m) => m.user_id === actor);
  return member ? member.display_name || member.email : actor;
}

async function loadPage(ctx, append) {
  const params = new URLSearchParams({ limit: "50", offset: String(offset) });
  if (actionFilter) params.set("action", actionFilter);
  const page = await request(projectApi(ctx.project.id, `/audit?${params.toString()}`));
  rows = append ? rows.concat(page) : page;
  return page;
}

// The vault is built by the server (verified sources only) and arrives as a zip; fetch it so a refusal
// (feature off, no access) shows as a message instead of navigating to a JSON error page.
async function downloadObsidianVault(ctx, button) {
  if (button.disabled) return;
  button.disabled = true;
  try {
    const response = await fetch(projectApi(ctx.project.id, "/export/obsidian"));
    if (response.status === 404) throw new Error("Obsidian export is not enabled on this server.");
    if (!response.ok) throw new Error("The Obsidian export failed.");
    const name = /filename="([^"]+)"/.exec(response.headers.get("Content-Disposition") || "")?.[1] || "obsidian-vault.zip";
    const url = URL.createObjectURL(await response.blob());
    const link = el("a", { href: url, attrs: { download: name } });
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 10000);
    toast("Vault downloaded. Unzip it and open the folder in Obsidian (verified sources only).");
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
  }
}

function purposeTable(usage) {
  const node = el("table", { className: "versions-table" });
  const head = el("tr");
  ["Purpose", "Calls", "Tokens"].forEach((h) => head.append(el("th", { text: h })));
  node.append(head);
  usage.by_purpose.forEach((row) => {
    const tr = el("tr");
    tr.append(el("td", { text: row.purpose }), el("td", { text: String(row.calls) }), el("td", { text: String(row.tokens) }));
    node.append(tr);
  });
  return node;
}

async function usagePanel(ctx) {
  const box = el("div", { className: "panel" });
  box.append(el("h2", { text: "Model usage" }));
  try {
    const usage = await request(projectApi(ctx.project.id, "/usage"));
    const summary = usageSummary(usage);
    const dl = el("dl", { className: "effective-settings" });
    summary.rows.forEach(([name, value]) => dl.append(el("dt", { text: name }), el("dd", { text: value })));
    box.append(dl);
    if (summary.notice) box.append(el("p", { className: `usage-notice usage-${summary.notice.tone}`, text: summary.notice.text }));
    if (usage.by_purpose.length > 0) box.append(purposeTable(usage));
    if (ctx.isOwner) {
      const row = el("div", { className: "form-actions" });
      const input = el("input", {
        attrs: {
          type: "number", min: "0", step: "1",
          placeholder: usage.default_budget === null ? "Use the default (no limit)" : `Use the default (${usage.default_budget})`,
        },
      });
      if (usage.budget_override !== null) input.value = String(usage.budget_override);
      const save = el("button", { text: "Save" });
      save.addEventListener("click", async () => {
        save.disabled = true;
        try {
          const override = input.value.trim() === "" ? null : Number(input.value);
          await request(projectApi(ctx.project.id, "/usage/budget"), { method: "PUT", body: JSON.stringify({ override }) });
          toast("Token budget saved.");
          box.replaceWith(await usagePanel(ctx));
        } catch (error) {
          toast(error.message);
        } finally {
          save.disabled = false;
        }
      });
      row.append(input, save);
      box.append(row);
      box.append(el("p", { className: "muted small", text: "Leave blank to use this server's default budget for every project. 0 means no limit for this project." }));
    }
  } catch (error) {
    box.append(el("p", { className: "muted small", text: error.message }));
  }
  return box;
}

// How well the AI matched this project's own people (X.80): recall and precision from their decisions, never a verdict.
async function accuracyPanel(ctx) {
  const box = el("div", { className: "panel" });
  box.append(el("h2", { text: "AI accuracy in this project" }));
  try {
    const report = await request(projectApi(ctx.project.id, "/accuracy-report"));
    if (!hasAnything(report)) {
      box.append(el("p", { className: "muted small", text: "Nothing to compare yet. Once you have decided papers the AI also suggested on, or verified AI extractions, the match shows here." }));
    }
    screeningLines(report.screening).forEach((line) => box.append(el("p", { children: [el("strong", { text: `${line.stage}: ` }), document.createTextNode(line.text)] })));
    extractionTables(report.extraction).forEach((scored) => {
      box.append(el("h3", { text: `Extraction, ${scored.schema} (${scored.papers} paper(s))` }));
      const grid = el("table", { className: "versions-table" });
      const head = el("tr");
      ["Field", "Precision", "Recall"].forEach((h) => head.append(el("th", { text: h })));
      grid.append(head);
      scored.rows.forEach((row) => {
        const tr = el("tr");
        [`${row.field}${row.critical ? " *" : ""}`, row.precision, row.recall].forEach((cell) => tr.append(el("td", { text: cell })));
        grid.append(tr);
      });
      box.append(grid);
    });
    extractionNotes(report.extraction).forEach((note) => box.append(el("p", { className: "muted small", text: note })));
    box.append(el("p", { className: "muted small", text: `* Critical field. ${report.caveat}` }));
  } catch (error) {
    box.append(el("p", { className: "muted small", text: error.message }));
  }
  return box;
}

function gridTable({ head, rows }) {
  const node = el("table", { className: "versions-table" });
  const headRow = el("tr");
  head.forEach((h) => headRow.append(el("th", { text: h })));
  node.append(headRow);
  rows.forEach((cells) => {
    const tr = el("tr");
    cells.forEach((cell) => tr.append(el("td", { text: cell })));
    node.append(tr);
  });
  return node;
}

// Failures list with an Open button for each research run (its run id is in the step payload). Other steps have no screen to open.
function failuresTable(ctx, data) {
  const { head, rows } = failureRows(data);
  const node = el("table", { className: "versions-table" });
  const headRow = el("tr");
  [...head, "Open"].forEach((h) => headRow.append(el("th", { text: h })));
  node.append(headRow);
  rows.forEach((cells, index) => {
    const tr = el("tr");
    cells.forEach((cell) => tr.append(el("td", { text: cell })));
    const cell = el("td");
    const runId = data.failures[index].run_id;
    if (runId) {
      const open = el("button", { className: "quiet-button", text: "Open" });
      open.addEventListener("click", () => {
        rememberRun(() => sessionStorage, FOCUS_RESEARCH_RUN_KEY, runId);
        ctx.navigate("ask");
      });
      cell.append(open);
    } else {
      cell.append(el("span", { className: "muted", text: "—" }));
    }
    tr.append(cell);
    node.append(tr);
  });
  return node;
}

// Owner-only observability view (X.7.3, approved walkthrough 2026-10-22): step outcomes, timings and model-call cost.
// Counts and names only; the server returns no error text. Co-authors, reviewers and supervisors do not see it (X.136).
async function observabilityPanel(ctx) {
  const box = el("div", { className: "panel", attrs: { id: "observability-panel" } });
  box.append(el("h2", { text: "Run metrics" }));
  try {
    const data = await request(projectApi(ctx.project.id, "/observability"));
    const grid = el("div", { className: "form-grid" });
    tiles(data).forEach((tile) => {
      grid.append(el("div", {
        className: "panel",
        children: [el("div", { className: "muted small", text: tile.label }), el("div", { text: tile.value }), el("div", { className: "muted small", text: tile.sub })],
      }));
    });
    box.append(grid);
    box.append(el("h3", { text: "Steps by type" }), gridTable(stepRows(data)));
    box.append(el("p", { className: "muted small", text: "Queued, running and paused are shown separately. Blocked = waiting for a gate approval. Retried = the step needed a second or later attempt." }));
    box.append(el("h3", { text: "How long completed steps took" }), gridTable(durationRows(data)));
    box.append(el("p", { className: "muted small", text: "Times use the attempt that finished. Steps that ran before timings were recorded show 0 timed." }));
    box.append(el("h3", { text: "Model calls and cost" }), gridTable(callRows(data)));
    box.append(el("p", { className: "muted small", text: "Costs use this server's price table. A model without a price shows a dash and is left out of the total. No prompts, answers or error text are shown." }));
    box.append(el("h3", { text: "Failures" }), failuresTable(ctx, data));
    box.append(el("p", { className: "muted small", text: `Showing ${data.failures.length} of ${data.steps_failed} failures. Error text is not shown here.` }));
  } catch (error) {
    box.append(el("p", { className: "muted small", text: error.message }));
  }
  return box;
}

function table(ctx) {
  const node = el("table", { className: "versions-table", attrs: { id: "activity-table" } });
  const head = el("tr");
  ["Time", "Actor", "Action", "Details"].forEach((h) => head.append(el("th", { text: h })));
  node.append(head);
  rows.forEach((event) => {
    const row = el("tr");
    const details = JSON.stringify(event.payload_json || {});
    const short = details.length > 160 ? `${details.slice(0, 160)}…` : details;
    const detailCell = el("td", { text: short, attrs: { title: details } });
    const extra = [event.model_id, event.prompt_version].filter(Boolean).join(" · ");
    row.append(
      el("td", { text: formatDate(event.timestamp) }),
      el("td", { text: actorName(ctx, event.actor) }),
      el("td", { text: event.action }),
      detailCell
    );
    if (extra) row.append(el("td", { className: "muted small", text: extra }));
    node.append(row);
  });
  return node;
}

// Owner-only controls for how far AI may take part in this project and which model it uses (X.31.7). Everyone else
// sees the current setting. A change is applied by the server and audited; the header chip updates through ctx.refresh.
async function aiSettingsPanel(ctx) {
  const box = el("div", { className: "panel", attrs: { id: "ai-settings-panel" } });
  box.append(el("h2", { text: "AI in this project" }));
  try {
    const current = await request(projectApi(ctx.project.id, "/ai-settings"));
    const level = aiLevel(current.ai_involvement);
    box.append(el("p", { text: level.label }), el("p", { className: "muted small", text: level.help }));
    const route = AI_ROUTES.find((item) => item.value === current.ai_model_route);
    box.append(el("p", { className: "muted small", text: `Model: ${route ? route.label : current.ai_model_route}` }));
    if (!ctx.isOwner) {
      box.append(el("p", { className: "muted small", text: "Only the project owner can change this." }));
      return box;
    }
    const levelSelect = el("select", { attrs: { "aria-label": "How far AI may take part" } });
    AI_LEVELS.forEach((item) => levelSelect.append(el("option", { text: item.label, attrs: { value: item.value } })));
    levelSelect.value = current.ai_involvement;
    const routeSelect = el("select", { attrs: { "aria-label": "Which model AI uses" } });
    AI_ROUTES.forEach((item) => {
      const option = el("option", { text: item.label, attrs: { value: item.value } });
      if (item.value === "local" && !current.local_model_configured) option.disabled = true;
      routeSelect.append(option);
    });
    routeSelect.value = current.ai_model_route;
    const save = el("button", { text: "Save", attrs: { type: "button" } });
    save.addEventListener("click", async () => {
      save.disabled = true;
      try {
        const saved = await request(projectApi(ctx.project.id, "/ai-settings"), {
          method: "PUT",
          body: JSON.stringify({ ai_involvement: levelSelect.value, ai_model_route: routeSelect.value }),
        });
        ctx.project.ai_involvement = saved.ai_involvement;
        ctx.project.ai_model_route = saved.ai_model_route;
        toast("AI settings saved.");
        await ctx.refresh();
      } catch (error) {
        toast(error.message);
      } finally {
        save.disabled = false;
      }
    });
    box.append(el("div", { className: "form-actions", children: [levelSelect, routeSelect, save] }));
    if (!current.local_model_configured) {
      box.append(el("p", { className: "muted small", text: "No local model is configured on this server, so only the standard model can be chosen." }));
    }
  } catch (error) {
    box.append(el("p", { className: "muted small", text: error.message }));
  }
  return box;
}

async function render(root, ctx) {
  if (currentProjectId !== ctx.project.id) {
    offset = 0;
    actionFilter = "";
    rows = [];
    currentProjectId = ctx.project.id;
  }
  const panel = el("div", { className: "panel" });
  const title = el("div", { className: "section-title" });
  title.append(el("div", { children: [el("h2", { text: "Activity" })] }));
  const exportLinks = el("div", { className: "source-actions" });
  exportLinks.append(
    el("a", { className: "quiet-button", text: "Export CSV", href: projectApi(ctx.project.id, "/audit/export?format=csv"), attrs: { download: "" } }),
    el("a", { className: "quiet-button", text: "Export JSON", href: projectApi(ctx.project.id, "/audit/export?format=json"), attrs: { download: "" } })
  );
  const obsidian = el("button", {
    className: "quiet-button",
    text: "Export to Obsidian",
    attrs: { type: "button", title: "Download a vault of verified sources, answers and context as Markdown notes" },
  });
  obsidian.addEventListener("click", () => downloadObsidianVault(ctx, obsidian));
  exportLinks.append(obsidian);
  title.append(exportLinks);
  panel.append(title);

  const filterRow = el("div", { className: "form-grid" });
  const filterInput = el("input", { attrs: { type: "text", placeholder: "Filter by action (exact)", value: actionFilter } });
  filterInput.addEventListener("change", async () => {
    actionFilter = filterInput.value.trim();
    offset = 0;
    await loadPage(ctx, false);
    render(root, ctx);
  });
  filterRow.append(filterInput);
  panel.append(filterRow);

  const page = await loadPage(ctx, false);
  panel.append(table(ctx));
  if (page.length === 50) {
    const more = el("button", { className: "quiet-button", text: "Load more" });
    more.addEventListener("click", async () => {
      offset += 50;
      const nextPage = await loadPage(ctx, true);
      panel.replaceChild(table(ctx), panel.querySelector("#activity-table"));
      if (nextPage.length < 50) more.remove();
    });
    panel.append(more);
  }

  const owner = ctx.isOwner ? [await observabilityPanel(ctx)] : [];
  root.replaceChildren(await aiSettingsPanel(ctx), await usagePanel(ctx), ...owner, await accuracyPanel(ctx), panel);
}

export default { render };
