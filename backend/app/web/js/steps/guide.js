// "Research path": what every stage and gate means, an advice-only review-type chooser, and when meta-analysis is
// advisable. All text comes from GET /api/guide (app/guides/journey.toml); the stage order comes from GET /stage
// `chain`. Nothing chosen here is saved or changes the project.
import { el } from "../dom.js";
import { request } from "../api.js";
import { STEPS, loadGuide } from "../workflow.js";
import { errorCard } from "./common.js";

function whereLine(ctx, stepId) {
  const step = stepId && STEPS.find((candidate) => candidate.id === stepId);
  if (!step) return el("p", { className: "muted small", text: "No screen in the app yet." });
  const line = el("p", { className: "muted small", text: "Where: " });
  const go = el("button", { className: "quiet-button", text: step.title });
  go.addEventListener("click", () => ctx.navigate(step.id));
  line.append(go);
  return line;
}

function journey(ctx, guide) {
  const panel = el("div", { className: "panel" });
  panel.append(el("h2", { text: "Your research path" }));
  const chain = ctx.summary.stage?.chain || [];
  const currentIndex = chain.findIndex((item) => item.stage === ctx.summary.stage?.stage);
  const list = el("ol", { className: "journey" });
  chain.forEach((item, index) => {
    const stage = guide.stages[item.stage];
    if (!stage) return;
    const position = index < currentIndex ? "done" : index === currentIndex ? "current" : "upcoming";
    const li = el("li", { className: `journey-stage ${position}` });
    if (position === "current") li.setAttribute("aria-current", "step");
    const heading = el("h3", { text: index === currentIndex ? `${stage.title} (you are here) ` : `${stage.title} ` });
    if (item.gate) heading.append(el("span", { className: "stage-gate", text: item.gate }));
    const details = el("details");
    details.append(el("summary", { text: "What is advisable" }), el("p", { text: stage.advice }));
    li.append(heading, el("p", { text: stage.meaning }), details);
    const gate = item.gate && guide.gates[item.gate];
    if (gate) li.append(el("p", { className: "muted small", text: `Gate ${item.gate} · ${gate.title}: ${gate.meaning}` }));
    li.append(whereLine(ctx, stage.step));
    list.append(li);
  });
  panel.append(list);
  return panel;
}

function bulletList(title, items) {
  const wrap = el("div");
  wrap.append(el("p", { className: "small", children: [el("strong", { text: title })] }));
  const list = el("ul", { className: "check-list" });
  items.forEach((text) => list.append(el("li", { text })));
  wrap.append(list);
  return wrap;
}

function pathChooser(guide) {
  const panel = el("div", { className: "panel" });
  panel.append(
    el("h2", { text: "Which path fits you?" }),
    el("p", { className: "muted", text: "Answer what you can. The paths that fit your answers best are marked." })
  );
  const methods = Object.fromEntries(guide.synthesis_methods.map((m) => [m.id, m]));
  const cards = new Map();
  const grid = el("div", { className: "path-grid" });
  guide.paths.forEach((path) => {
    const card = el("div", { className: "path-card" });
    const fit = el("p", { className: "eyebrow", text: "FITS YOUR ANSWERS" });
    fit.hidden = true;
    card.append(fit, el("h3", { text: path.title }), el("p", { text: path.summary }));
    card.append(bulletList("Choose this when", path.choose_when), bulletList("You will need", path.you_need));
    if (path.reporting_guideline) card.append(el("p", { className: "small", text: `Reporting guideline: ${path.reporting_guideline}` }));
    card.append(el("p", { className: "small", text: `Synthesis: ${methods[path.synthesis]?.title || path.synthesis}` }));
    card.append(el("p", { className: "muted small", text: `In this app: ${path.in_this_app}` }));
    cards.set(path.id, { card, fit });
    grid.append(card);
  });

  const form = el("form", { className: "path-questions" });
  form.addEventListener("submit", (event) => event.preventDefault());
  const rescore = () => {
    const scores = new Map([...cards.keys()].map((id) => [id, 0]));
    guide.questions.forEach((question) => {
      const picked = form.querySelector(`input[name="q-${question.id}"]:checked`);
      if (!picked) return;
      question.answers[Number(picked.value)].points_to.forEach((id) => scores.set(id, scores.get(id) + 1));
    });
    const best = Math.max(...scores.values());
    cards.forEach(({ card, fit }, id) => {
      const fits = best > 0 && scores.get(id) === best;
      card.classList.toggle("fit", fits);
      fit.hidden = !fits;
    });
  };
  guide.questions.forEach((question) => {
    const fieldset = el("fieldset");
    fieldset.append(el("legend", { text: question.text }));
    question.answers.forEach((answer, index) => {
      const input = el("input", { type: "radio", attrs: { name: `q-${question.id}`, value: String(index) } });
      input.addEventListener("change", rescore);
      fieldset.append(el("label", { className: "radio-line", children: [input, document.createTextNode(` ${answer.label}`)] }));
    });
    form.append(fieldset);
  });
  panel.append(form, grid);
  return panel;
}

function synthesisGuide(guide) {
  const panel = el("div", { className: "panel" });
  panel.append(
    el("h2", { text: "Is meta-analysis right for you?" }),
    el("p", { className: "muted", text: "How you combine the evidence depends on what your studies report. Compare the options below." })
  );
  const grid = el("div", { className: "path-grid" });
  guide.synthesis_methods.forEach((method) => {
    const card = el("div", { className: "method-card" });
    card.append(
      el("h3", { text: `${method.title} ` }),
      el("span", {
        className: `level-badge ${method.available ? "level-information" : "level-recommended"}`,
        text: method.available ? "Available" : "Not built yet",
      }),
      el("p", { text: method.what }),
      bulletList("Advisable when", method.advisable_when),
      bulletList("Not advisable when", method.not_advisable_when),
      el("p", { className: "muted small", text: `In this app: ${method.in_this_app}` })
    );
    grid.append(card);
  });
  panel.append(grid);
  return panel;
}

async function render(root, ctx) {
  let guide;
  try {
    guide = await loadGuide(ctx, request);
  } catch (error) {
    root.replaceChildren(errorCard(error.message, ctx.refresh));
    return;
  }
  root.replaceChildren(
    el("p", { className: "muted", text: "Advice only. Nothing you pick here is saved or changes this project's stages. Discuss your choice with your supervisor." }),
    journey(ctx, guide),
    pathChooser(guide),
    synthesisGuide(guide)
  );
}

export default { render };
