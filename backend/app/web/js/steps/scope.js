import { $, el, label, toast, emptyCard } from "../dom.js";
import { request, projectApi } from "../api.js";
import { gateCard } from "./common.js";
import { renderFrame } from "./frame.js";
import { canChangeResearchType, canSaveResearchType, chainRows, researchTypeLockReason, selectedResearchType } from "../researchTypeLogic.js";

const KIND_ORDER = ["question", "objective", "hypothesis", "methodology", "variable", "decision"];

function contextEditor(item, ctx) {
  const form = el("form", { className: "context-edit" });
  const statement = el("textarea", { attrs: { name: "content", required: "", "aria-label": "Statement" } });
  statement.value = item.content;
  const reason = el("textarea", { attrs: { name: "rationale", "aria-label": "Why does this matter?", placeholder: "Why does this matter? (optional)" } });
  reason.value = item.rationale || "";
  const save = el("button", { type: "submit", text: "Save changes" });
  const cancel = el("button", { type: "button", className: "quiet-button", text: "Cancel" });
  cancel.addEventListener("click", () => ctx.refresh());
  form.append(statement, reason, el("div", { className: "dialog-actions", children: [cancel, save] }));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    save.disabled = true;
    const body = { content: statement.value.trim() };
    if (reason.value.trim() !== (item.rationale || "")) body.rationale = reason.value.trim() || null;
    try {
      await request(projectApi(ctx.project.id, `/context/${item.id}`), { method: "PATCH", body: JSON.stringify(body) });
      toast("Entry updated. The earlier wording is kept in its history.");
      await ctx.refresh();
    } catch (error) {
      save.disabled = false;
      toast(error.message);
    }
  });
  return form;
}

function contextActions(card, item, ctx) {
  const edit = el("button", { type: "button", className: "quiet-button", text: "Edit" });
  edit.addEventListener("click", () => {
    card.replaceChildren(contextEditor(item, ctx));
    card.querySelector("textarea").focus();
  });
  const retire = el("button", { type: "button", className: "quiet-button", text: "Retire" });
  retire.addEventListener("click", async () => {
    if (!window.confirm("Retire this entry? It is hidden from the project and from the AI, but kept in the audit record.")) return;
    try {
      await request(projectApi(ctx.project.id, `/context/${item.id}/retire`), { method: "POST" });
      toast("Entry retired.");
      await ctx.refresh();
    } catch (error) {
      toast(error.message);
    }
  });
  const remove = el("button", { type: "button", className: "quiet-button danger", text: "Delete" });
  remove.addEventListener("click", async () => {
    if (!window.confirm("Delete this entry permanently? This cannot be undone. To keep a record of it, use Retire instead.")) return;
    try {
      await request(projectApi(ctx.project.id, `/context/${item.id}`), { method: "DELETE" });
      toast("Entry deleted.");
      await ctx.refresh();
    } catch (error) {
      toast(error.message);
    }
  });
  return el("div", { className: "context-actions", children: [edit, retire, remove] });
}

function contextCard(item, ctx) {
  const leftOut = item.used_by_agent === false;
  const card = el("article", { className: leftOut ? "context-card not-sent" : "context-card" });
  card.append(
    el("span", {
      className: "context-kind",
      text: leftOut ? `${label(item.kind)} · Not sent to the agent` : label(item.kind),
    }),
    el("h3", { text: item.content })
  );
  card.append(el("p", { text: item.rationale ? `Reason: ${item.rationale}` : "Saved to this project's structured research memory." }));
  if (item.history?.length) card.append(el("p", { className: "muted small", text: `Edited ${item.history.length} time${item.history.length === 1 ? "" : "s"}; earlier wording is kept.` }));
  if (ctx.canWrite) card.append(contextActions(card, item, ctx));
  return card;
}

function openAddContext() {
  const form = $("#context-form");
  form.reset();
  $("#context-dialog").showModal();
}

async function discipline(root, ctx) {
  const box = el("div", { className: "panel" });
  box.append(el("h2", { text: "Discipline profile" }));
  try {
    if (!ctx.cache.profiles) {
      ctx.cache.profiles = await request("/api/discipline-profiles");
    }
    const current = await request(projectApi(ctx.project.id, "/profile"));
    if (ctx.isOwner) {
      const select = el("select", { attrs: { name: "discipline" } });
      select.append(el("option", { text: "None", attrs: { value: "" } }));
      ctx.cache.profiles.forEach((profile) => {
        const option = el("option", { text: profile.label, attrs: { value: profile.name } });
        if (current.discipline === profile.name) option.selected = true;
        select.append(option);
      });
      const save = el("button", { text: "Save" });
      save.addEventListener("click", async () => {
        save.disabled = true;
        try {
          await request(projectApi(ctx.project.id, "/profile"), {
            method: "PUT",
            body: JSON.stringify({ discipline: select.value || null }),
          });
          toast("Discipline profile saved.");
          await ctx.refresh();
        } catch (error) {
          toast(error.message);
        } finally {
          save.disabled = false;
        }
      });
      const row = el("div", { className: "form-actions" });
      row.append(select, save);
      box.append(row);
    }
    const dl = el("dl", { className: "effective-settings" });
    const entries = [
      ["Citation style", current.effective.citation_style],
      ["Databases", current.effective.databases?.join(", ")],
      ["Methods", current.effective.methods?.join(", ")],
      ["Reporting guideline", current.effective.reporting_guideline],
    ];
    entries.forEach(([term, value]) => {
      dl.append(el("dt", { text: term }), el("dd", { text: value || "Not set" }));
    });
    box.append(dl);
  } catch (error) {
    box.append(el("p", { className: "muted small", text: error.message }));
  }
  return box;
}

// Research type picker (X.43.9): which stages this project goes through. The owner can change it only at idea
// with no approved gate; everyone else sees the saved type and its stage list. The server re-checks both rules.
async function researchTypePanel(ctx) {
  const box = el("div", { className: "panel" });
  box.append(el("h2", { text: "Research type" }));
  try {
    if (!ctx.cache.researchTypes) {
      ctx.cache.researchTypes = await request("/api/research-types");
    }
    const types = ctx.cache.researchTypes;
    const stage = ctx.summary.stage || {};
    const saved = stage.research_type;
    const changeable = canChangeResearchType({ isOwner: ctx.isOwner, stage: ctx.summary.stage?.stage, gates: ctx.summary.gates || [] });
    const lockReason = researchTypeLockReason({ isOwner: ctx.isOwner, stage: ctx.summary.stage?.stage, gates: ctx.summary.gates || [] });
    box.append(el("p", { className: "muted small", text: changeable
      ? "Choose the type before any gate is approved. It decides which stages this project passes through."
      : lockReason }));
    if (changeable) {
      const picker = el("div", { className: "type-options" });
      const state = { picked: saved };
      const save = el("button", { text: "Save research type" });
      const saveNote = el("span", { className: "muted small" });
      const sync = () => {
        const canSave = canSaveResearchType({ saved, picked: state.picked, changeable });
        save.disabled = !canSave;
        saveNote.textContent = canSave ? "" : !state.picked ? "Pick a type to save." : "Already saved.";
      };
      types.forEach((type) => {
        const option = el("label", { className: "type-option" });
        const input = el("input", { attrs: { type: "radio", name: "research-type", value: type.name } });
        input.checked = type.name === selectedResearchType({ saved, picked: state.picked });
        input.addEventListener("change", () => {
          state.picked = type.name;
          sync();
        });
        option.append(input, el("strong", { text: ` ${type.label}` }), el("span", { className: "muted small", text: ` · ${type.stages.length} stages` }));
        picker.append(option);
      });
      save.addEventListener("click", async () => {
        save.disabled = true;
        try {
          await request(projectApi(ctx.project.id, "/research-type"), {
            method: "PUT",
            body: JSON.stringify({ name: state.picked }),
          });
          toast("Research type saved.");
          await ctx.refresh();
        } catch (error) {
          toast(error.message);
          sync();
        }
      });
      sync();
      const row = el("div", { className: "form-actions" });
      row.append(save, saveNote);
      box.append(picker, row);
    } else {
      // Locked (wireframe Q3): every type stays visible, greyed out, with the reason above.
      const picker = el("div", { className: "type-options" });
      types.forEach((type) => {
        const option = el("label", { className: "type-option is-disabled" });
        const input = el("input", { attrs: { type: "radio", name: "research-type", value: type.name } });
        input.checked = type.name === saved;
        input.disabled = true;
        option.append(input, el("strong", { text: ` ${type.label}` }), el("span", { className: "muted small", text: ` · ${type.stages.length} stages` }));
        picker.append(option);
      });
      box.append(picker);
    }
    const rows = chainRows(stage.chain);
    const list = el("ol", { className: "research-type-stages" });
    rows.forEach((row) => {
      const text = `${row.label || label(row.stage)}${row.gate ? ` · ${row.gate}` : ""}${row.mandatory ? " (required in every type)" : ""}`;
      list.append(el("li", { text }));
    });
    box.append(el("p", { className: "muted small", text: `Stages in this project (${rows.length}):` }), list);
  } catch (error) {
    box.append(el("p", { className: "muted small", text: error.message }));
  }
  return box;
}

async function render(root, ctx) {
  const items = (ctx.summary.context || []).filter((item) => item.kind !== "idea");
  const sections = [];

  const contextPanel = el("div", { className: "panel context-panel" });
  const title = el("div", { className: "section-title" });
  title.append(el("div", { children: [el("h2", { text: "Structured context" })] }));
  if (ctx.canWrite) {
    const add = el("button", { text: "Add context" });
    add.addEventListener("click", openAddContext);
    title.append(add);
  }
  contextPanel.append(title);
  if (items.length === 0) {
    contextPanel.append(emptyCard("Capture your first research decision", "Add a question, objective, or methodological choice to make this project easier to continue later."));
  } else {
    const list = el("div", { className: "context-list" });
    KIND_ORDER.forEach((kind) => {
      items.filter((item) => item.kind === kind).forEach((item) => list.append(contextCard(item, ctx)));
    });
    contextPanel.append(list);
  }
  sections.push(contextPanel);

  sections.push(await discipline(root, ctx));
  sections.push(await researchTypePanel(ctx));

  const gatePanel = el("div", { className: "panel" });
  gatePanel.append(el("h2", { text: "Approval" }));
  gatePanel.append(gateCard(ctx, "G1"));
  sections.push(gatePanel);

  renderFrame(root, ctx, { stepId: "scope", work: sections, next: ["scoped"] });
}

export default { render };
