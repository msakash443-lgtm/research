import { el, label, emptyCard } from "../dom.js";

const STATUS_LABEL = { found: "Found", missing: "Missing", not_searched: "Not searched", cannot_check: "Cannot check" };

function prismaBox(ctx) {
  const box = el("div", { className: "panel" });
  box.append(el("h2", { text: "PRISMA counts" }));
  const prisma = ctx.summary.prisma;
  if (!prisma) {
    box.append(el("p", { className: "muted small", text: "Not available." }));
    return box;
  }
  const id = prisma.identification;
  box.append(
    el("p", {
      text: `Identified: ${id.identified} · Duplicates removed: ${id.duplicates_removed} · After duplicates removed: ${id.after_duplicates_removed}`,
    })
  );
  const table = el("table", { className: "versions-table" });
  const head = el("tr");
  ["Database", "v", "Identified", "Capped", "Kind"].forEach((h) => head.append(el("th", { text: h })));
  table.append(head);
  (id.searches || []).forEach((search) => {
    const row = el("tr");
    row.append(
      el("td", { text: label(search.database) }),
      el("td", { text: String(search.version) }),
      el("td", { text: String(search.identified) }),
      el("td", { text: search.truncated ? "Capped" : "" }),
      el("td", { text: search.exact ? "Exact" : "Approximate" })
    );
    table.append(row);
  });
  box.append(table);
  if (prisma.warnings?.length) {
    const warnings = el("ul", { className: "warning-list" });
    prisma.warnings.forEach((w) => warnings.append(el("li", { text: w })));
    box.append(warnings);
  }
  box.append(el("p", { className: "muted small", text: "Screening counts appear once screening is built." }));
  return box;
}

function knownItemsBox(ctx) {
  const box = el("div", { className: "panel" });
  box.append(el("h2", { text: "Seed recall check" }));
  const seeds = ctx.summary.seeds || [];
  if (seeds.length === 0) {
    box.append(emptyCard("No seed papers", "Add seed papers in Criteria & seeds to check whether the search finds them."));
    return box;
  }
  const known = ctx.summary.knownItems;
  if (!known) {
    box.append(el("p", { className: "muted small", text: "Not available." }));
    return box;
  }
  const recall = known.recall_of_seeds == null ? "—" : `${Math.round(known.recall_of_seeds * 100)}%`;
  box.append(el("p", { text: `Found ${known.found} of ${known.checked} checked seeds (recall ${recall}).` }));
  if (known.flagged) {
    const warnings = el("ul", { className: "warning-list" });
    (known.flags || []).forEach((f) => warnings.append(el("li", { text: f })));
    box.append(warnings);
  }
  const list = el("ul", { className: "gate-list" });
  (known.items || []).forEach((item) => {
    const row = el("li", { className: "gate-row" });
    row.append(
      el("span", { className: "gate-title", text: item.title }),
      el("span", { className: "gate-status", text: STATUS_LABEL[item.status] || label(item.status) })
    );
    const note = item.reason || item.note;
    if (note) row.append(el("p", { className: "small muted gate-note", text: note }));
    list.append(row);
  });
  box.append(list);
  return box;
}

function render(root, ctx) {
  root.replaceChildren(prismaBox(ctx), knownItemsBox(ctx));
}

export default { render };
