import { el, label } from "../dom.js";
import { request } from "../api.js";
import { STAGES, STEPS, GATE_NAMES, stepStatus, loadGuide, stageLabel } from "../workflow.js";
import { errorCard } from "./common.js";
import { renderFrame, LEVEL_LABEL } from "./frame.js";

const CHECKLIST_IDS = ["scope", "criteria", "search", "results", "sources", "ask"];
const STATUS_LABEL = { done: "Done", attention: "Needs attention", todo: "Not started", info: "" };

function progressLine(ctx) {
  const stage = ctx.summary.stage?.stage;
  const idx = STAGES.findIndex(([name]) => name === stage);
  return `Stage ${idx + 1} of ${STAGES.length} · ${idx >= 0 ? stageLabel(STAGES[idx]) : label(stage || "")}`;
}

async function whereYouAre(ctx) {
  let guide;
  try {
    guide = await loadGuide(ctx, request);
  } catch (error) {
    return errorCard(error.message, ctx.refresh);
  }
  const stage = guide.stages[ctx.summary.stage?.stage];
  const box = el("div", { className: "next-step" });
  box.append(el("p", { className: "eyebrow", text: "WHERE YOU ARE" }));
  if (stage) {
    box.append(
      el("h3", { text: stage.title }),
      el("p", { text: stage.meaning }),
      el("p", { className: "muted", text: `What is advisable: ${stage.advice}` })
    );
  }
  const more = el("button", { className: "quiet-button", text: "See the whole research path" });
  more.addEventListener("click", () => ctx.navigate("guide"));
  box.append(more);
  return box;
}

// Built on the server from the real exit conditions of the next move (GET /stage `suggestions`, app/guidance.py).
function suggestedSteps(ctx) {
  const panel = el("div", { className: "panel" });
  panel.append(el("h2", { text: "Suggested next steps" }));
  const suggestions = ctx.summary.stage?.suggestions || [];
  if (!suggestions.length) {
    const done = (ctx.summary.stage?.next || []).length === 0;
    panel.append(el("p", {
      className: "muted",
      text: done
        ? "This project has reached the last stage."
        : "Nothing is blocking the next stage. Use the card below to move on, or open Research path to see what comes after.",
    }));
    return panel;
  }
  const list = el("ol", { className: "suggestion-list" });
  suggestions.forEach((s) => {
    const item = el("li", { className: "suggestion" });
    const message = s.message.charAt(0).toUpperCase() + s.message.slice(1);
    item.append(
      el("span", { className: `level-badge level-${s.level}`, text: LEVEL_LABEL[s.level] || label(s.level) }),
      el("strong", { text: ` ${message}` })
    );
    if (s.meaning) item.append(el("p", { text: `Why it matters: ${s.meaning}` }));
    if (s.fix) item.append(el("p", { className: "muted small", text: `Where: ${s.fix}` }));
    if (s.level === "recommended") {
      item.append(el("p", { className: "muted small", text: "You can move on without this, but you will be asked to write a reason." }));
    }
    const step = s.step && STEPS.find((candidate) => candidate.id === s.step);
    if (step) {
      const go = el("button", { className: "quiet-button", text: `Go to ${step.title}` });
      go.addEventListener("click", () => ctx.navigate(step.id));
      item.append(go);
    } else {
      item.append(el("p", { className: "muted small", text: "This has no screen in the app yet." }));
    }
    list.append(item);
  });
  panel.append(list);
  return panel;
}

async function render(root, ctx) {
  const sections = [];
  sections.push(el("p", { className: "muted", text: progressLine(ctx) }));
  sections.push(await whereYouAre(ctx));
  sections.push(suggestedSteps(ctx));

  const options = ctx.summary.stage?.next || [];

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
  renderFrame(root, ctx, { stepId: "overview", work: sections, next: options.map((option) => option.stage) });
}

export default { render };
