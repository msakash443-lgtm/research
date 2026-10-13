import { el, label } from "../dom.js";
import { STEPS } from "../workflow.js";
import { advanceCard } from "./common.js";

// The step template (docs/wireframes/step-template-checklist.md): every step screen has the same six parts.
//   1 Purpose            — rendered by the shell above #step-body (app.js renderStep, from GUIDES[stepId].what)
//   2 My work area       — `work`, the screen's own nodes
//   3 AI suggestions     — `ai`, a node or null; null renders nothing (the panel is hidden, e.g. AI setting Off)
//   4 One next button    — `next`, stage names looked up in GET /stage `next`; rendered by advanceCard
//   5 Back / revisit     — "Previous step" link, for every role
//   6 Lock / conditions  — the unmet Required/Recommended exit conditions of those next moves (X.48.2)
// Information-level conditions are hints only and are not shown here.

export const LEVEL_LABEL = { required: "Must do", recommended: "Advisable", information: "Good practice" };

// The journey is the screens in reading order; Research path and the Project group are not part of it.
const JOURNEY = STEPS.filter((step) => step.id !== "guide" && step.group !== "Project");

export function previousStep(stepId) {
  const index = JOURNEY.findIndex((step) => step.id === stepId);
  return index > 0 ? JOURNEY[index - 1] : null;
}

function unmetConditions(options) {
  const seen = new Set();
  const unmet = [];
  options.forEach((option) => {
    (option.conditions || []).forEach((condition) => {
      if (condition.met || condition.level === "information" || seen.has(condition.id)) return;
      seen.add(condition.id);
      unmet.push(condition);
    });
  });
  // Required before Recommended, so the binding one is read first.
  return unmet.sort((a, b) => (a.level === b.level ? 0 : a.level === "required" ? -1 : 1));
}

function conditionBox(conditions) {
  const box = el("div", { className: "next-step condition-box" });
  box.append(el("h3", { text: "Before you move on" }));
  const list = el("ul", { className: "condition-list" });
  conditions.forEach((condition) => {
    const item = el("li", { className: `condition condition-${condition.level}` });
    const message = condition.message.charAt(0).toUpperCase() + condition.message.slice(1);
    item.append(
      el("span", { className: `level-badge level-${condition.level}`, text: LEVEL_LABEL[condition.level] || label(condition.level) }),
      el("strong", { text: ` ${message}` })
    );
    if (condition.level === "recommended") {
      item.append(el("p", { className: "muted small", text: "You can move on without this, but you will be asked to write a reason." }));
    }
    if (condition.fix) item.append(el("p", { className: "muted small", text: `Where: ${condition.fix}` }));
    list.append(item);
  });
  box.append(list);
  return box;
}

export function renderFrame(root, ctx, { stepId, work, ai = null, next = [] }) {
  const options = next
    .map((stage) => (ctx.summary.stage?.next || []).find((option) => option.stage === stage))
    .filter(Boolean);
  const nodes = [];

  const conditions = unmetConditions(options);
  if (conditions.length) nodes.push(conditionBox(conditions));

  nodes.push(...work);
  if (ai) nodes.push(ai);

  const footer = el("div", { className: "step-footer" });
  const previous = previousStep(stepId);
  if (previous) {
    footer.append(el("a", { className: "back-link", href: `#/p/${ctx.project.id}/${previous.id}`, text: `Previous step: ${previous.title}` }));
  }
  if (options.length) {
    const nextWrap = el("div", { className: "step-next" });
    options.forEach((option) => nextWrap.append(advanceCard(ctx, option)));
    footer.append(nextWrap);
  }
  if (footer.childElementCount) nodes.push(footer);

  root.replaceChildren(...nodes);
}
