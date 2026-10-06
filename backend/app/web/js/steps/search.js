import { $, el, label, toast, saveOnce } from "../dom.js";
import { request, projectApi } from "../api.js";
import { SEARCHABLE } from "../workflow.js";
import { gateCard, advanceCard } from "./common.js";

const ACCESS_LABEL = { official_api: "Official API", licensed: "Licensed", scraping: "Scraping" };

function connectorState(connector) {
  if (connector.usable) return "Ready";
  if (connector.implemented && !connector.enabled) return "Off on this server";
  if (!connector.implemented) return "Not built yet";
  return "Unusable";
}

async function connectorsBox(ctx) {
  const box = el("div", { className: "panel" });
  box.append(el("h2", { text: "Databases" }));
  try {
    if (!ctx.cache.connectors) ctx.cache.connectors = await request("/api/connectors");
    const list = el("ul", { className: "gate-list" });
    ctx.cache.connectors.forEach((connector) => {
      const row = el("li", { className: "gate-row" });
      row.append(
        el("span", { className: "gate-title", text: connector.label }),
        el("span", { className: "gate-code", text: ACCESS_LABEL[connector.access] || connector.access }),
        el("span", { className: "gate-status", text: connectorState(connector) })
      );
      if (connector.note) row.append(el("p", { className: "small muted gate-note", text: connector.note }));
      list.append(row);
    });
    box.append(list);
  } catch (error) {
    box.append(el("p", { className: "muted small", text: error.message }));
  }
  return box;
}

function conceptBlockRow(container) {
  const row = el("li", { className: "concept-block" });
  const labelInput = el("input", { attrs: { type: "text", maxlength: "100", placeholder: "Concept label (optional)" } });
  const terms = el("textarea", { attrs: { placeholder: "One term per line", rows: "3", required: "" } });
  const remove = el("button", { className: "quiet-button", type: "button", text: "Remove" });
  remove.addEventListener("click", () => row.remove());
  row.append(labelInput, terms, remove);
  row._labelInput = labelInput;
  row._terms = terms;
  container.append(row);
  return row;
}

async function openSearchDialog(ctx) {
  const dialog = $("#search-dialog");
  const form = $("#search-form");
  form.reset();
  const databaseSelect = form.elements.database;
  databaseSelect.replaceChildren();
  if (!ctx.cache.connectors) ctx.cache.connectors = await request("/api/connectors");
  const usable = ctx.cache.connectors.filter((c) => c.usable && SEARCHABLE.includes(c.name));
  const submitButton = form.querySelector('button[type="submit"]');
  if (usable.length === 0) {
    databaseSelect.append(el("option", { text: "No searchable database enabled", attrs: { value: "" } }));
    submitButton.disabled = true;
  } else {
    submitButton.disabled = false;
    usable.forEach((c) => databaseSelect.append(el("option", { text: c.label, attrs: { value: c.name } })));
  }
  const blocksContainer = $("#search-blocks");
  blocksContainer.replaceChildren();
  conceptBlockRow(blocksContainer);
  $("#search-add-concept").onclick = () => {
    if (blocksContainer.children.length >= 10) return;
    conceptBlockRow(blocksContainer).querySelector("textarea").focus();
  };

  form.onsubmit = (event) => {
    event.preventDefault();
    saveOnce(form, async () => {
      const blocks = Array.from(blocksContainer.querySelectorAll(".concept-block"))
        .map((row) => ({
          label: row._labelInput.value.trim() || undefined,
          terms: row._terms.value.split("\n").map((t) => t.trim()).filter(Boolean),
        }))
        .filter((block) => block.terms.length > 0);
      const filters = {};
      if (form.elements.year_from.value) filters.year_from = form.elements.year_from.value;
      if (form.elements.year_to.value) filters.year_to = form.elements.year_to.value;
      const body = {
        database: databaseSelect.value,
        blocks,
        filters,
        max_results: Number(form.elements.max_results.value) || 500,
      };
      try {
        const queued = await request(projectApi(ctx.project.id, "/searches"), { method: "POST", body: JSON.stringify(body) });
        dialog.close();
        toast(
          queued.status === "blocked"
            ? "Search saved. It runs after a person approves G2 (Search plan)."
            : "Search queued."
        );
        await ctx.refresh();
      } catch (error) {
        toast(error.message);
      }
    });
  };
  dialog.showModal();
}

function searchesList(root, ctx) {
  const list = root.querySelector("#search-results-list") || el("div", { className: "source-list", attrs: { id: "search-results-list" } });
  const data = ctx.summary.searches || { searches: [], pending: [] };
  const cards = [];
  data.searches.forEach((search) => {
    const card = el("article", { className: "source-card" });
    const header = el("div", { className: "run-heading" });
    header.append(el("span", { text: label(search.database) }));
    header.append(el("span", { className: "gate-status", text: search.exact ? "Exact" : "Approximate" }));
    card.append(header);
    card.append(el("p", { children: [el("code", { text: search.query_string })] }));
    if (!search.exact && search.caveats?.length) {
      const caveats = el("ul", { className: "warning-list" });
      search.caveats.forEach((c) => caveats.append(el("li", { text: c })));
      card.append(caveats);
    }
    const table = el("table", { className: "versions-table" });
    const head = el("tr");
    ["v", "Run", "Results", "Unique", "Dupes removed"].forEach((h) => head.append(el("th", { text: h })));
    table.append(head);
    (search.versions || []).forEach((v) => {
      const row = el("tr");
      row.append(
        el("td", { text: String(v.version) }),
        el("td", { text: v.run_at ? new Date(v.run_at).toLocaleDateString() : "—" }),
        el("td", { text: v.n_results ?? "—" }),
        el("td", { text: v.counts?.unique ?? "—" }),
        el("td", { text: v.counts?.duplicates_removed ?? "—" })
      );
      table.append(row);
    });
    card.append(table);
    if (ctx.canWrite) {
      const pendingHere = data.pending.some((p) => p.search_id === search.search_id);
      const rerun = el("button", { className: "quiet-button", text: "Re-run" });
      rerun.disabled = pendingHere;
      rerun.addEventListener("click", async () => {
        rerun.disabled = true;
        try {
          await request(projectApi(ctx.project.id, `/searches/${search.search_id}/rerun`), { method: "POST" });
          toast("Search re-queued.");
          await ctx.refresh();
        } catch (error) {
          toast(error.message);
          rerun.disabled = false;
        }
      });
      card.append(rerun);
    }
    cards.push(card);
  });
  if (data.pending.length) {
    const pendingBox = el("div", { className: "panel" });
    pendingBox.append(el("h3", { text: "Pending" }));
    data.pending.forEach((p) => {
      const line = p.blocked_by_gate
        ? `v${p.version} · ${label(p.database)} — Waiting for G2 (Search plan) approval`
        : `v${p.version} · ${label(p.database)} — ${label(p.status)}`;
      pendingBox.append(el("p", { className: "muted small", text: line }));
    });
    cards.push(pendingBox);
  }
  list.replaceChildren(...cards);
  return list;
}

async function render(root, ctx) {
  const sections = [];
  sections.push(await connectorsBox(ctx));

  const searchesPanel = el("div", { className: "panel" });
  const title = el("div", { className: "section-title" });
  title.append(el("div", { children: [el("h2", { text: "Searches" })] }));
  if (ctx.canWrite) {
    const add = el("button", { text: "New search" });
    add.addEventListener("click", () => openSearchDialog(ctx));
    title.append(add);
  }
  searchesPanel.append(title);
  searchesPanel.append(searchesList(root, ctx));
  sections.push(searchesPanel);

  const gatePanel = el("div", { className: "panel" });
  gatePanel.append(el("h2", { text: "Approval" }));
  gatePanel.append(gateCard(ctx, "G2"));
  const option = (ctx.summary.stage?.next || []).find((o) => o.stage === "search_planned");
  if (option) gatePanel.append(advanceCard(ctx, option));
  sections.push(gatePanel);

  root.replaceChildren(...sections);
}

function update(root, ctx) {
  const current = root.querySelector("#search-results-list");
  if (current) searchesList(root, ctx);
}

export default { render, update };
