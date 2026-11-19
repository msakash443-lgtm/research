import { $, el, label, toast, saveOnce, formatDate } from "../dom.js";
import { request, projectApi } from "../api.js";
import { STAGES, stageLabel } from "../workflow.js";
import { actorName } from "../provenance.js";
import { gateCard } from "./common.js";
import { screeningReviewPanel } from "./screeningReview.js";
import { focusStatus, gateGroups, stageProgress } from "../approvalsLogic.js";

const HISTORY_TEXT = {
  "project.advanced": (p) => `Moved forward: ${label(p.from)} → ${label(p.to)}`,
  "project.reentered": (p) =>
    p.from === p.to ? `Reopened ${label(p.to)} for revision` : `Went back: ${label(p.from)} → ${label(p.to)}`,
  "stage.condition_overridden": (p) => `Overrode condition ${p.condition} (${label(p.to)})`,
  "gate.reset": (p) => `Approval of ${p.gate} voided (${p.because})`,
  "gate.reopened": (p) => `Reopened ${p.gate}`,
  "gate.approved": (p) => `Approved ${p.gate}`,
  "gate.rejected": (p) => `Rejected ${p.gate}`,
};

function historyLine(ctx, event) {
  const payload = event.payload_json || {};
  const what = (HISTORY_TEXT[event.action] || (() => event.action))(payload);
  const why = payload.reason ? `Reason: ${payload.reason}` : payload.note ? `Note: ${payload.note}` : "";
  const item = el("li");
  item.append(el("strong", { text: what }));
  item.append(el("span", { className: "muted small", text: ` · ${actorName(ctx, event.actor)} · ${formatDate(event.timestamp)}` }));
  if (why) item.append(el("p", { className: "muted small", text: why }));
  return item;
}

async function historyPanel(ctx) {
  const panel = el("div", { className: "panel" });
  panel.append(el("h2", { text: "Stage history" }));
  let events = [];
  try {
    events = await request(projectApi(ctx.project.id, "/stage/history"));
  } catch (error) {
    panel.append(el("p", { className: "muted small", text: error.message }));
    return panel;
  }
  if (!events.length) {
    panel.append(el("p", { className: "muted small", text: "No stage moves or approvals yet." }));
    return panel;
  }
  const list = el("ul", { className: "warning-list" });
  [...events].reverse().forEach((event) => list.append(historyLine(ctx, event)));
  panel.append(list);
  return panel;
}

function stageTrack(ctx) {
  const stage = ctx.summary.stage?.stage;
  const currentIndex = STAGES.findIndex(([name]) => name === stage);
  const track = el("ol", { className: "stage-track", attrs: { "aria-label": "Workflow stages" } });
  STAGES.forEach((entry, index) => {
    const [, gate] = entry;
    const kind = index < currentIndex ? "done" : index === currentIndex ? "current" : "upcoming";
    const item = el("li", { className: `stage-step ${kind}` });
    if (kind === "current") item.setAttribute("aria-current", "step");
    item.append(el("span", { className: "stage-name", text: stageLabel(entry) }));
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

export function openReenterDialog(ctx) {
  const dialog = $("#reenter-dialog");
  const form = $("#reenter-form");
  form.reset();
  const select = form.elements.stage;
  select.replaceChildren();
  const currentIndex = STAGES.findIndex(([name]) => name === ctx.summary.stage?.stage);
  STAGES.slice(0, currentIndex + 1).forEach((entry) => select.append(el("option", { text: stageLabel(entry), attrs: { value: entry[0] } })));
  select.value = ctx.summary.stage?.stage;
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
          `Reopened ${label(result.to_stage)}. ${result.gates_reset.length} gate(s) reset, ${result.stale_artifacts.length} result(s) marked stale.`
        );
        await ctx.refresh();
      } catch (error) {
        toast(error.message);
      }
    });
  };
  dialog.showModal();
}

function gateRows(ctx, gates) {
  const list = el("ul", { className: "gate-list" });
  gates.forEach((gate) => {
    gateCard(ctx, gate.code).querySelectorAll("li").forEach((row) => list.append(row));
  });
  return list;
}

// What to decide now (X.218): the one actionable gate with its prerequisites, in full. Every other gate stays
// reachable below; gate enforcement is the server's and is unchanged.
function decisionPanel(ctx, groups) {
  const panel = el("div", { className: "panel approvals-focus" });
  panel.append(el("h2", { text: groups.focus ? "Decision needed now" : "Approvals" }));
  panel.append(el("p", { className: "muted small", text: `${focusStatus(groups.focus)} Only a person can approve a gate.` }));
  if (groups.focus) panel.append(gateRows(ctx, [groups.focus]));
  if (groups.attention.length) {
    panel.append(el("h3", { text: "Rejected gates" }), gateRows(ctx, groups.attention));
  }
  return panel;
}

function collapsed(title, gates, ctx, openCode) {
  const details = el("details", { className: "approvals-collapsed" });
  details.open = gates.some((gate) => gate.code === openCode);
  details.append(el("summary", { text: `${title} (${gates.length})` }), gateRows(ctx, gates));
  return details;
}

// `openCode`: a gate whose own review panel is on the page; its section starts open so the button that panel points to is in view.
function gatesPanel(ctx, groups, openCode) {
  const panel = el("div", { className: "panel" });
  panel.append(el("h2", { text: "Other gates" }));
  if (groups.done.length) panel.append(collapsed("Approved gates", groups.done, ctx, openCode));
  if (groups.later.length) panel.append(collapsed("Later gates", groups.later, ctx, openCode));
  if (!groups.done.length && !groups.later.length) panel.append(el("p", { className: "muted small", text: "No other gates." }));
  return panel;
}

function stagesPanel(ctx) {
  const progress = stageProgress(STAGES, ctx.summary.stage?.stage);
  const panel = el("div", { className: "panel" });
  panel.append(el("h2", { text: "Workflow stages" }));
  panel.append(el("p", { className: "muted small", text: `${progress.done} of ${progress.total} stages done · now: ${stageLabel(STAGES[progress.currentIndex])}` }));
  const details = el("details", { className: "approvals-collapsed" });
  details.append(el("summary", { text: `All ${progress.total} stages` }), stageTrack(ctx));
  panel.append(details);
  return panel;
}

async function render(root, ctx) {
  const sections = [];
  const groups = gateGroups(ctx.summary.gates, ctx.summary.stage?.stage, STAGES);
  sections.push(decisionPanel(ctx, groups));

  if ((ctx.summary.staleArtifacts || []).length > 0) {
    let rerunPath = [];
    try {
      rerunPath = await request(projectApi(ctx.project.id, "/rerun-path"));
    } catch {
      // the stale-list still shows without the detailed path
    }
    sections.push(staleWarning(ctx, rerunPath));
  }

  const review = await screeningReviewPanel(ctx);
  if (review) sections.push(review);

  sections.push(gatesPanel(ctx, groups, review ? "G3" : null));
  sections.push(stagesPanel(ctx));

  const currentIndex = STAGES.findIndex(([name]) => name === ctx.summary.stage?.stage);
  if (ctx.isOwner && currentIndex >= 0) {
    const reenterPanel = el("div", { className: "panel" });
    reenterPanel.append(el("h2", { text: "Reopen a stage" }));
    reenterPanel.append(
      el("p", {
        className: "muted small",
        text: "Reopen the current stage or go back to an earlier one. That stage's gate and every later gate return to pending, and its results and later results are marked stale. Nothing is deleted.",
      })
    );
    const open = el("button", { className: "quiet-button", text: "Reopen…" });
    open.addEventListener("click", () => openReenterDialog(ctx));
    reenterPanel.append(open);
    sections.push(reenterPanel);
  }

  sections.push(await historyPanel(ctx));

  root.replaceChildren(...sections);
}

export default { render };
