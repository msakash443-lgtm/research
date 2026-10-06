import { $, el, label, toast, emptyCard, confirmAction, saveOnce } from "../dom.js";
import { request, projectApi } from "../api.js";
import { FRAMEWORK_ELEMENTS } from "../workflow.js";

function elementOptions(framework, selected) {
  const options = [el("option", { text: "—", attrs: { value: "" } })];
  (FRAMEWORK_ELEMENTS[framework] || []).forEach((name) => {
    const option = el("option", { text: label(name), attrs: { value: name } });
    if (name === selected) option.selected = true;
    options.push(option);
  });
  return options;
}

function buildRow(rowsContainer, frameworkSelect, criterion) {
  const row = el("li", { className: "criteria-row" });
  if (criterion?.code) row.dataset.code = criterion.code;
  const kind = el("select", { attrs: { "aria-label": "Kind" } });
  kind.append(el("option", { text: "Include", attrs: { value: "include" } }), el("option", { text: "Exclude", attrs: { value: "exclude" } }));
  kind.value = criterion?.kind || "include";
  const elementSelect = el("select", { attrs: { "aria-label": "Framework element" } });
  elementSelect.append(...elementOptions(frameworkSelect.value, criterion?.element));
  elementSelect.hidden = frameworkSelect.value === "custom";
  const text = el("input", {
    attrs: { type: "text", minlength: "3", maxlength: "1000", required: "", "aria-label": "Criterion text" },
  });
  text.value = criterion?.text || "";
  const remove = el("button", { className: "quiet-button", type: "button", text: "Remove" });
  remove.addEventListener("click", () => row.remove());
  row.append(kind, elementSelect, text, remove);
  row._kindInput = kind;
  row._elementInput = elementSelect;
  row._textInput = text;
  rowsContainer.append(row);
  return row;
}

function openCriteriaDialog(ctx) {
  const dialog = $("#criteria-dialog");
  const form = $("#criteria-form");
  const frameworkSelect = form.elements.framework;
  const rowsContainer = $("#criteria-rows");
  rowsContainer.replaceChildren();
  const current = ctx.summary.criteria;
  frameworkSelect.value = current?.framework || "pico";
  (current?.criteria || []).forEach((criterion) => buildRow(rowsContainer, frameworkSelect, criterion));

  frameworkSelect.onchange = () => {
    rowsContainer.querySelectorAll(".criteria-row").forEach((row) => {
      const valid = (FRAMEWORK_ELEMENTS[frameworkSelect.value] || []).includes(row._elementInput.value);
      row._elementInput.replaceChildren(...elementOptions(frameworkSelect.value, valid ? row._elementInput.value : ""));
      row._elementInput.hidden = frameworkSelect.value === "custom";
    });
  };
  $("#criteria-add-include").onclick = () => {
    const row = buildRow(rowsContainer, frameworkSelect, { kind: "include" });
    row._textInput.focus();
  };
  $("#criteria-add-exclude").onclick = () => {
    const row = buildRow(rowsContainer, frameworkSelect, { kind: "exclude" });
    row._textInput.focus();
  };

  form.onsubmit = (event) => {
    event.preventDefault();
    saveOnce(form, async () => {
      const rows = Array.from(rowsContainer.querySelectorAll(".criteria-row"));
      const criteria = rows.map((row) => ({
        kind: row._kindInput.value,
        text: row._textInput.value.trim(),
        element: row._elementInput.value || undefined,
        code: row.dataset.code || undefined,
      }));
      try {
        await request(projectApi(ctx.project.id, "/criteria"), {
          method: "PUT",
          body: JSON.stringify({ framework: frameworkSelect.value, criteria }),
        });
        dialog.close();
        toast("Criteria saved.");
        await ctx.refresh();
      } catch (error) {
        toast(error.message);
      }
    });
  };
  dialog.showModal();
}

function openSeedDialog(ctx) {
  const dialog = $("#seed-dialog");
  const form = $("#seed-form");
  form.reset();
  form.onsubmit = (event) => {
    event.preventDefault();
    saveOnce(form, async () => {
      const data = Object.fromEntries(new FormData(form));
      if (!data.doi) delete data.doi;
      if (!data.note) delete data.note;
      if (data.year) data.year = Number(data.year);
      else delete data.year;
      data.authors = data.authors ? data.authors.split(",").map((a) => a.trim()).filter(Boolean) : undefined;
      try {
        await request(projectApi(ctx.project.id, "/seeds"), { method: "POST", body: JSON.stringify(data) });
        dialog.close();
        toast("Seed paper added.");
        await ctx.refresh();
      } catch (error) {
        toast(error.message);
      }
    });
  };
  dialog.showModal();
}

function criteriaTable(title, rows) {
  const box = el("div", { className: "criteria-table" });
  box.append(el("h3", { text: title }));
  if (rows.length === 0) {
    box.append(el("p", { className: "muted small", text: "None yet." }));
    return box;
  }
  const list = el("ul", { className: "criteria-list" });
  rows.forEach((criterion) => {
    const row = el("li");
    row.append(el("span", { className: "gate-code", text: criterion.code }));
    row.append(el("span", { text: criterion.text }));
    if (criterion.element) row.append(el("span", { className: "muted small", text: label(criterion.element) }));
    list.append(row);
  });
  box.append(list);
  return box;
}

function render(root, ctx) {
  const criteria = ctx.summary.criteria;
  const sections = [];

  const header = el("div", { className: "panel" });
  const title = el("div", { className: "section-title" });
  title.append(
    el("div", {
      children: [
        el("h2", { text: `Criteria · ${criteria?.framework ? label(criteria.framework) : "Not chosen"}` }),
        criteria?.locked
          ? el("span", { className: "gate-code", text: "Locked — G2 approved" })
          : el("span", { className: "muted small", text: "Editable until G2 is approved" }),
      ],
    })
  );
  if (ctx.canWrite && !criteria?.locked) {
    const edit = el("button", { text: "Edit criteria" });
    edit.addEventListener("click", () => openCriteriaDialog(ctx));
    title.append(edit);
  }
  header.append(title);
  if (criteria?.problems?.length) {
    const warnings = el("ul", { className: "warning-list" });
    criteria.problems.forEach((p) => warnings.append(el("li", { text: p })));
    header.append(warnings);
  }
  const include = (criteria?.criteria || []).filter((c) => c.kind === "include");
  const exclude = (criteria?.criteria || []).filter((c) => c.kind === "exclude");
  header.append(criteriaTable("Include", include), criteriaTable("Exclude", exclude));
  sections.push(header);

  const seedsPanel = el("div", { className: "panel" });
  const seedTitle = el("div", { className: "section-title" });
  seedTitle.append(el("div", { children: [el("h2", { text: "Seed papers" })] }));
  if (ctx.canWrite) {
    const add = el("button", { text: "Add seed paper" });
    add.addEventListener("click", () => openSeedDialog(ctx));
    seedTitle.append(add);
  }
  seedsPanel.append(seedTitle);
  const seeds = ctx.summary.seeds || [];
  if (seeds.length === 0) {
    seedsPanel.append(emptyCard("No seed papers yet", "Add papers you know should be found by the search, to check recall later."));
  } else {
    const list = el("div", { className: "source-list" });
    seeds.forEach((seed) => {
      const card = el("article", { className: "source-card" });
      card.append(el("h3", { text: seed.title }));
      const meta = [seed.doi, seed.year, (seed.authors || []).join(", ")].filter(Boolean).join(" · ");
      if (meta) card.append(el("p", { className: "source-locator", text: meta }));
      if (seed.note) card.append(el("p", { className: "evidence-excerpt", text: seed.note }));
      if (ctx.canWrite) {
        const remove = el("button", { className: "quiet-button", text: "Remove" });
        remove.addEventListener("click", async () => {
          const ok = await confirmAction("Remove seed paper?", `"${seed.title}" will no longer be checked for recall.`, "Remove");
          if (!ok) return;
          try {
            await request(projectApi(ctx.project.id, `/seeds/${seed.id}`), { method: "DELETE" });
            await ctx.refresh();
          } catch (error) {
            toast(error.message);
          }
        });
        card.append(remove);
      }
      list.append(card);
    });
    seedsPanel.append(list);
  }
  sections.push(seedsPanel);

  root.replaceChildren(...sections);
}

export default { render };
