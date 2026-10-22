import { $, el, label, toast, emptyCard } from "../dom.js";
import { request, projectApi } from "../api.js";
import { gateCard } from "./common.js";
import { renderFrame } from "./frame.js";
import { canChangeResearchType, canSaveResearchType, chainRows, researchTypeLockReason, selectedResearchType } from "../researchTypeLogic.js";

const KIND_ORDER = ["question", "objective", "hypothesis", "methodology", "variable", "decision"];

function contextCard(item) {
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
      const sync = () => {
        save.disabled = !canSaveResearchType({ saved, picked: state.picked, changeable });
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
      row.append(save);
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
      items.filter((item) => item.kind === kind).forEach((item) => list.append(contextCard(item)));
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
