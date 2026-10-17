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

const STALE_WORK = {
  criteria: new Set(["criteria"]),
  results: new Set(["search_run"]),
  sources: new Set(["search_run"]),
  screen: new Set(["screening_decision"]),
  evidence: new Set(["extraction"]),
  verification: new Set(["extraction"]),
  ask: new Set(["research_run"]),
};

export function staleWorkBanner(ctx, stepId) {
  const kinds = STALE_WORK[stepId];
  if (!kinds) return null;
  const stale = (ctx.summary.staleArtifacts || []).filter((artifact) => kinds.has(artifact.kind));
  if (!stale.length) return null;
  const box = el("div", { className: "next-step stale-work" });
  box.append(el("h3", { text: "Needs redoing" }));
  const list = el("ul", { className: "warning-list" });
  stale.forEach((artifact) =>
    list.append(el("li", { text: artifact.stale_reason || "Earlier work changed; redo this step before relying on it." }))
  );
  box.append(list);
  return box;
}

// The journey is the screens in reading order; Research path and the Project group are not part of it.
const JOURNEY = STEPS.filter((step) => step.id !== "guide" && step.group !== "Project");

export function previousStep(stepId) {
  const index = JOURNEY.findIndex((step) => step.id === stepId);
  return index > 0 ? JOURNEY[index - 1] : null;
}

// Look up a journey screen by id, for a step-to-step "Next: <screen>" link (X.41.4) where the next
// move is a specific screen, not a `GET /stage` stage-advance option (Sources has no stage of its own).
function stepById(id) {
  return JOURNEY.find((step) => step.id === id) || null;
}

function stepLinkCard(ctx, step) {
  const box = el("div", { className: "next-option" });
  box.append(el("p", { text: `Next: ${step.title}` }));
  box.append(el("a", { className: "back-link", href: `#/p/${ctx.project.id}/${step.id}`, text: `Go to ${step.title}` }));
  return box;
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

function fixHref(ctx, fix) {
  if (!fix) return null;
  if (/^Approve\b/i.test(fix)) return `#/p/${ctx.project.id}/approvals`;
  const match = fix.match(/^([^:]+):/);
  if (!match) return null;
  const routes = {
    Scope: "scope",
    Criteria: "criteria",
    Search: "search",
    "Results check": "results",
    Results: "results",
    Sources: "sources",
    Screening: "screen",
    "Extraction review": "approvals",
    Extraction: "evidence",
    Verification: "verification",
    Ask: "ask",
    Write: "write",
    Approvals: "approvals",
  };
  const key = match[1].trim();
  if (!Object.hasOwn(routes, key)) return null;
  return `#/p/${ctx.project.id}/${routes[key]}`;
}

function conditionBox(ctx, conditions) {
  const box = el("div", { className: "next-step condition-box" });
  box.append(el("h3", { text: "Before you move on" }));
  const list = el("ul", { className: "condition-list" });
  conditions.forEach((condition) => {
    const item = el("li", { className: `condition condition-${condition.level}` });
    const message = condition.message;
    item.append(
      el("span", { className: `level-badge level-${condition.level}`, text: LEVEL_LABEL[condition.level] || label(condition.level) }),
      el("strong", { text: ` ${message}` })
    );
    if (condition.level === "recommended") {
      item.append(el("p", { className: "muted small", text: "You can move on without this, but you will be asked to write a reason." }));
    }
    const href = fixHref(ctx, condition.fix);
    if (condition.fix) {
      item.append(el("p", { className: "muted small", text: `Where: ${condition.fix}` }));
      if (href) {
        item.append(el("a", {
          className: "back-link condition-fix",
          href,
          text: "Fix this",
          attrs: { "aria-label": `Fix: ${condition.message}` },
        }));
      }
    }
    list.append(item);
  });
  box.append(list);
  return box;
}

export function conditionFixLink(ctx, condition) {
  const href = fixHref(ctx, condition.fix);
  return href ? { href, text: "Fix this" } : null;
}

export function renderFrame(root, ctx, { stepId, work, ai = null, next = [], stepNext = null }) {
  const options = next
    .map((stage) => (ctx.summary.stage?.next || []).find((option) => option.stage === stage))
    .filter(Boolean);
  const nodes = [];

  const conditions = unmetConditions(options);
  if (conditions.length) nodes.push(conditionBox(ctx, conditions));
  const stale = staleWorkBanner(ctx, stepId);
  if (stale) nodes.push(stale);
  nodes.push(...work);
  if (ai) nodes.push(ai);

  const footer = el("div", { className: "step-footer" });
  const previous = previousStep(stepId);
  if (previous) {
    footer.append(el("a", { className: "back-link", href: `#/p/${ctx.project.id}/${previous.id}`, text: `Previous step: ${previous.title}` }));
  }
  const forward = stepNext ? stepById(stepNext) : null;
  if (options.length || forward) {
    const nextWrap = el("div", { className: "step-next" });
    options.forEach((option) => nextWrap.append(advanceCard(ctx, option)));
    if (forward) nextWrap.append(stepLinkCard(ctx, forward));
    footer.append(nextWrap);
  }
  if (footer.childElementCount) nodes.push(footer);

  root.replaceChildren(...nodes);
}
