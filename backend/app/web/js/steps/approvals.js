import { $, el, label, toast, saveOnce } from "../dom.js";
import { request, projectApi } from "../api.js";
import { STAGES } from "../workflow.js";
import { gateCard } from "./common.js";

function stageTrack(ctx) {
  const stage = ctx.summary.stage?.stage;
  const currentIndex = STAGES.findIndex(([name]) => name === stage);
  const track = el("ol", { className: "stage-track", attrs: { "aria-label": "Workflow stages" } });
  STAGES.forEach(([name, gate], index) => {
    const kind = index < currentIndex ? "done" : index === currentIndex ? "current" : "upcoming";
    const item = el("li", { className: `stage-step ${kind}` });
    if (kind === "current") item.setAttribute("aria-current", "step");
    item.append(el("span", { className: "stage-name", text: label(name) }));
    if (gate) item.append(el("span", { className: "stage-gate", text: gate }));
    track.append(item);
  });
  return track;
}

function staleWarning(ctx, rerunPath) {
  const box = el("div", { className: "next-step" });
  box.append(el("h3", { text: "Stale results" }));
  const list = el("ul", { className: "warning-list" });
  ctx.summary.staleArtifacts.forEach((artifact) => {
    list.append(el("li", { text: `${artifact.kind} (${label(artifact.stage)}): ${artifact.stale_reason || "stale"}` }));
  });
  box.append(list);
  if (rerunPath?.length) {
    const table = el("table", { className: "versions-table" });
    const head = el("tr");
    ["Stage", "Gate", "Status", "Artifacts to redo"].forEach((h) => head.append(el("th", { text: h })));
    table.append(head);
    rerunPath.forEach((step) => {
      const row = el("tr");
      row.append(
        el("td", { text: label(step.stage) }),
        el("td", { text: step.gate || "—" }),
        el("td", { text: step.gate_status ? label(step.gate_status) : "—" }),
        el("td", { text: String((step.artifacts || []).length) })
      );
      table.append(row);
    });
    box.append(table);
  }
  return box;
}

function openReenterDialog(ctx) {
  const dialog = $("#reenter-dialog");
  const form = $("#reenter-form");
  form.reset();
  const select = form.elements.stage;
  select.replaceChildren();
  const currentIndex = STAGES.findIndex(([name]) => name === ctx.summary.stage?.stage);
  STAGES.slice(0, currentIndex).forEach(([name]) => select.append(el("option", { text: label(name), attrs: { value: name } })));
  form.onsubmit = (event) => {
    event.preventDefault();
    saveOnce(form, async () => {
      try {
        const result = await request(projectApi(ctx.project.id, "/stage/reenter"), {
          method: "POST",
          body: JSON.stringify({ stage: select.value, reason: form.elements.reason.value.trim() }),
        });
        dialog.close();
        toast(
          `Moved back to ${label(result.to_stage)}. ${result.gates_reset.length} gate(s) reset, ${result.stale_artifacts.length} result(s) marked stale.`
        );
        await ctx.refresh();
      } catch (error) {
        toast(error.message);
      }
    });
  };
  dialog.showModal();
}

async function render(root, ctx) {
  const sections = [];
  const trackPanel = el("div", { className: "panel" });
  trackPanel.append(el("h2", { text: "Workflow stages" }));
  trackPanel.append(stageTrack(ctx));
  sections.push(trackPanel);

  if ((ctx.summary.staleArtifacts || []).length > 0) {
    let rerunPath = [];
    try {
      rerunPath = await request(projectApi(ctx.project.id, "/rerun-path"));
    } catch {
      // the stale-list still shows without the detailed path
    }
    sections.push(staleWarning(ctx, rerunPath));
  }

  const gatesPanel = el("div", { className: "panel" });
  const title = el("div", { className: "section-title" });
  title.append(el("div", { children: [el("h2", { text: "Approval gates" })] }));
  title.append(el("p", { className: "muted small", text: "Only a person can approve a gate." }));
  gatesPanel.append(title);
  const list = el("ul", { className: "gate-list" });
  (ctx.summary.gates || []).forEach((gate) => {
    gateCard(ctx, gate.code).querySelectorAll("li").forEach((row) => list.append(row));
  });
  gatesPanel.append(list);
  sections.push(gatesPanel);

  const currentIndex = STAGES.findIndex(([name]) => name === ctx.summary.stage?.stage);
  if (ctx.isOwner && currentIndex > 0) {
    const reenterPanel = el("div", { className: "panel" });
    reenterPanel.append(el("h2", { text: "Go back to an earlier stage" }));
    reenterPanel.append(
      el("p", {
        className: "muted small",
        text: "Gates of the stages being redone return to pending; later results are marked stale.",
      })
    );
    const open = el("button", { className: "quiet-button", text: "Go back…" });
    open.addEventListener("click", () => openReenterDialog(ctx));
    reenterPanel.append(open);
    sections.push(reenterPanel);
  }

  root.replaceChildren(...sections);
}

export default { render };
