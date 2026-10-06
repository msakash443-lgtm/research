import { $, el, formatDate } from "../dom.js";
import { request, projectApi } from "../api.js";

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

  root.replaceChildren(panel);
}

export default { render };
