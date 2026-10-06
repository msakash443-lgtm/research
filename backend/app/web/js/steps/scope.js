import { $, el, label, toast, emptyCard } from "../dom.js";
import { request, projectApi } from "../api.js";
import { gateCard, advanceCard } from "./common.js";

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

  const gatePanel = el("div", { className: "panel" });
  gatePanel.append(el("h2", { text: "Approval" }));
  gatePanel.append(gateCard(ctx, "G1"));
  const scopedOption = (ctx.summary.stage?.next || []).find((option) => option.stage === "scoped");
  if (scopedOption) gatePanel.append(advanceCard(ctx, scopedOption));
  sections.push(gatePanel);

  root.replaceChildren(...sections);
}

export default { render };
