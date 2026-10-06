import { el, label } from "../dom.js";
import { STAGES, STEPS, GATE_NAMES, stepStatus, nextAction } from "../workflow.js";
import { advanceCard } from "./common.js";

const CHECKLIST_IDS = ["scope", "criteria", "search", "results", "sources", "ask"];
const STATUS_LABEL = { done: "Done", attention: "Needs attention", todo: "Not started", info: "" };

function progressLine(ctx) {
  const stage = ctx.summary.stage?.stage;
  const idx = STAGES.findIndex(([name]) => name === stage);
  return `Stage ${idx + 1} of ${STAGES.length} · ${label(stage || "")}`;
}

function render(root, ctx) {
  const sections = [];
  sections.push(el("p", { className: "muted", text: progressLine(ctx) }));

  const next = nextAction(ctx.summary);
  const nextBox = el("div", { className: "next-step" });
  nextBox.append(el("p", { className: "eyebrow", text: "NEXT ACTION" }));
  if (next) {
    nextBox.append(el("h3", { text: next.action }));
    const go = el("button", { text: `Go to ${next.title}` });
    go.addEventListener("click", () => ctx.navigate(next.id));
    nextBox.append(go);
  } else {
    nextBox.append(el("h3", { text: "The planning and gathering steps are complete." }));
  }
  sections.push(nextBox);

  const options = ctx.summary.stage?.next || [];
  if (options.length) {
    const advanceWrap = el("div", { className: "process-grid" });
    options.forEach((option) => advanceWrap.append(advanceCard(ctx, option)));
    sections.push(advanceWrap);
  }

  const waiting = options.filter((option) => {
    const gate = option.gate && (ctx.summary.gates || []).find((g) => g.code === option.gate);
    return gate && gate.status === "pending" && gate.can_decide;
  });
  if (waiting.length) {
    const box = el("div", { className: "next-step" });
    box.append(el("h3", { text: "Waiting on you" }));
    waiting.forEach((option) => box.append(el("p", { text: `${option.gate} · ${GATE_NAMES[option.gate]}` })));
    const link = el("button", { className: "quiet-button", text: "Go to Approvals" });
    link.addEventListener("click", () => ctx.navigate("approvals"));
    box.append(link);
    sections.push(box);
  }

  const checklistWrap = el("div", { className: "panel" });
  checklistWrap.append(el("h2", { text: "Checklist" }));
  const checklist = el("ul", { className: "checklist" });
  CHECKLIST_IDS.forEach((id) => {
    const { status, count } = stepStatus(id, ctx.summary);
    const step = STEPS.find((s) => s.id === id);
    const item = el("li", { className: `checklist-item status-${status}` });
    const link = el("a", { href: `#/p/${ctx.project.id}/${id}`, text: step.title });
    item.append(link);
    const statusText = STATUS_LABEL[status] + (count ? ` (${count})` : "");
    if (statusText) item.append(el("span", { className: "muted small", text: statusText }));
    checklist.append(item);
  });
  checklistWrap.append(checklist);
  sections.push(checklistWrap);

  root.replaceChildren(...sections);
}

export default { render };
