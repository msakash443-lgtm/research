import { el, emptyCard, label, toast } from "../dom.js";
import { projectApi, request } from "../api.js";
import { renderFrame } from "./frame.js";
import { gateCard } from "./common.js";
import {
  GAP_TYPES,
  finerStatusCounts,
  gapEvidenceReady,
  gapPayload,
  hypothesisPayload,
  researchQuestionPayload,
} from "../strategyLogic.js";

function field(labelText, control) {
  const wrapper = el("label", { className: "strategy-field" });
  wrapper.append(el("span", { text: labelText }), control);
  return wrapper;
}

function errorMessage(error) {
  return el("p", { className: "strategy-error", text: error.message, attrs: { role: "alert" } });
}

async function saveAndRefresh(ctx, path, method, payload) {
  await request(projectApi(ctx.project.id, path), { method, body: JSON.stringify(payload) });
  toast("Saved.");
  await ctx.refresh();
}

function sourceLabel(source) {
  const details = [source.year, source.venue].filter(Boolean).join(" · ");
  return details ? `${source.title} (${details})` : source.title;
}

function paperChecklist(sources, selectedIds = []) {
  const fieldset = el("fieldset", { className: "strategy-checklist" });
  fieldset.append(el("legend", { text: "Supporting papers — choose at least two distinct active sources" }));
  sources.forEach((source) => {
    const checkbox = el("input", { type: "checkbox", attrs: { value: source.id } });
    checkbox.checked = selectedIds.includes(String(source.id));
    const item = el("label", { className: "checkbox-label" });
    item.append(checkbox, el("span", { text: sourceLabel(source) }));
    fieldset.append(item);
  });
  return fieldset;
}

function checkedValues(container) {
  return [...container.querySelectorAll('input[type="checkbox"]:checked')].map((input) => input.value);
}

function createGapForm(ctx, sources) {
  if (!ctx.canWrite) return null;
  if (sources.length < 2) return emptyCard("Two active sources required", "Add at least two active project sources before recording a literature gap.");

  const box = el("div", { className: "panel" });
  box.append(el("h2", { text: "Record a gap" }));
  const form = el("form", { className: "strategy-form gap-form" });
  const type = el("select", { attrs: { name: "type", required: "" } });
  GAP_TYPES.forEach((value) => type.append(el("option", { text: label(value), attrs: { value } })));
  const statement = el("textarea", { attrs: { required: "", maxlength: "10000", "aria-label": "Describe the literature gap" } });
  const priority = el("input", { type: "number", attrs: { min: "0", max: "100", value: "0", "aria-label": "Priority" } });
  const papers = paperChecklist(sources);
  const save = el("button", { type: "submit", text: "Add gap" });
  form.append(field("Gap type", type), field("Gap statement", statement), papers, field("Priority (0–100)", priority), save);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const supportingPaperIds = checkedValues(papers);
    if (!gapEvidenceReady(supportingPaperIds)) {
      toast("Choose at least two distinct active sources as evidence.");
      return;
    }
    save.disabled = true;
    try {
      await saveAndRefresh(ctx, "/gaps", "POST", gapPayload({
        type: type.value, statement: statement.value, supportingPaperIds, priority: priority.value,
      }));
    } catch (error) {
      toast(error.message);
    } finally {
      save.disabled = false;
    }
  });
  box.append(form);
  return box;
}

function gapCard(ctx, gap, sources) {
  const card = el("article", { className: "gaps-item" });
  const heading = el("div", { className: "strategy-item-heading" });
  heading.append(el("h3", { text: gap.statement }), el("span", {
    className: gap.selected ? "status-chip status-selected" : "status-chip",
    text: gap.selected ? "Selected" : "Candidate",
  }));
  card.append(heading, el("p", { className: "muted small", text: `${label(gap.type)} · Priority ${gap.priority}` }));
  const sourceById = new Map(sources.map((source) => [String(source.id), source]));
  const evidence = (gap.supporting_paper_ids || []).map((id) => sourceById.get(String(id))?.title || "Project source");
  if (evidence.length) card.append(el("p", { className: "muted small", text: `Evidence: ${evidence.join("; ")}` }));
  if (ctx.canWrite) {
    const form = el("form", { className: "strategy-inline gap-curation" });
    const selected = el("input", { type: "checkbox", attrs: { "aria-label": "Select this gap for research questions" } });
    selected.checked = gap.selected;
    const toggle = el("label", { className: "checkbox-label" });
    toggle.append(selected, el("span", { text: "Selected for research-question linking" }));
    const priority = el("input", { type: "number", attrs: { min: "0", max: "100", value: String(gap.priority), "aria-label": "Gap priority" } });
    const save = el("button", { type: "submit", text: "Save curation" });
    form.append(toggle, field("Priority (0–100)", priority), save);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      save.disabled = true;
      try {
        await saveAndRefresh(ctx, `/gaps/${gap.id}`, "PATCH", { selected: selected.checked, priority: Number(priority.value) });
      } catch (error) {
        toast(error.message);
      } finally {
        save.disabled = false;
      }
    });
    card.append(form);
  }
  return card;
}

function selectedGapChecklist(gaps, linkedIds = []) {
  const fieldset = el("fieldset", { className: "strategy-checklist selected-gap-checklist" });
  fieldset.append(el("legend", { text: "Link selected literature gaps (optional)" }));
  const selected = gaps.filter((gap) => gap.selected);
  if (!selected.length) fieldset.append(el("p", { className: "muted small", text: "Select a curated gap above to link it to a research question." }));
  selected.forEach((gap) => {
    const checkbox = el("input", { type: "checkbox", attrs: { value: gap.id } });
    checkbox.checked = linkedIds.includes(String(gap.id));
    const item = el("label", { className: "checkbox-label" });
    item.append(checkbox, el("span", { text: gap.statement }));
    fieldset.append(item);
  });
  return fieldset;
}

function questionForm(ctx, gaps, question = null) {
  if (!ctx.canWrite) return null;
  const form = el("form", { className: "strategy-form question-form" });
  const text = el("textarea", { attrs: { required: "", maxlength: "20000", "aria-label": "Research question" } });
  text.value = question?.text || "";
  const type = el("input", { type: "text", attrs: { required: "", maxlength: "30", placeholder: "e.g. explanatory", "aria-label": "Research question type" } });
  type.value = question?.type || "";
  const gapLinks = selectedGapChecklist(gaps, question?.gap_ids || []);
  const save = el("button", { type: "submit", text: question ? "Save question" : "Add research question" });
  form.append(field("Research question", text), field("Question type", type), gapLinks, save);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    save.disabled = true;
    const payload = researchQuestionPayload({ text: text.value, type: type.value, gapIds: checkedValues(gapLinks) });
    try {
      await saveAndRefresh(ctx, question ? `/research-questions/${question.id}` : "/research-questions", question ? "PATCH" : "POST", payload);
    } catch (error) {
      toast(error.message);
    } finally {
      save.disabled = false;
    }
  });
  return form;
}

function finerPanel(ctx, question) {
  const box = el("div", { className: "finer-panel" });
  const run = el("button", { type: "button", text: "Run FINER critique" });
  const output = el("div", { className: "finer-output", attrs: { "aria-live": "polite" } });
  run.addEventListener("click", async () => {
    run.disabled = true;
    output.replaceChildren(el("p", { className: "muted", text: "Preparing advisory critique…" }));
    try {
      const critique = await request(projectApi(ctx.project.id, `/research-questions/${question.id}/finer`));
      const counts = finerStatusCounts(critique);
      const summary = el("p", { className: "muted small", text: `${counts.needsHumanReview} criteria need human review · ${counts.linkedToSelectedGaps} reflect selected-gap links · ${counts.missingGapLink} lack a gap link.` });
      const list = el("ul", { className: "finer-criteria" });
      critique.items.forEach((item) => {
        const row = el("li");
        row.append(el("strong", { text: `${label(item.criterion)} — ${label(item.status)}` }), el("span", { text: item.guidance }));
        list.append(row);
      });
      output.replaceChildren(summary, list, el("p", { className: "muted small", text: critique.disclaimer }));
    } catch (error) {
      output.replaceChildren(errorMessage(error));
    } finally {
      run.disabled = false;
    }
  });
  box.append(run, output);
  return box;
}

function questionCard(ctx, question, gaps) {
  const card = el("article", { className: "rq-card" });
  card.append(el("div", { className: "strategy-item-heading", children: [
    el("h3", { text: question.text }), el("span", { className: "status-chip", text: label(question.type) }),
  ] }));
  const linked = (question.gap_ids || []).map((id) => gaps.find((gap) => String(gap.id) === String(id))?.statement || "Previously linked gap");
  card.append(el("p", { className: "muted small", text: `Linked gaps: ${linked.length ? linked.join("; ") : "None"}` }));
  if (ctx.canWrite) {
    const details = el("details", { className: "strategy-edit-details" });
    details.append(el("summary", { text: "Edit research question and gap links" }), questionForm(ctx, gaps, question));
    card.append(details);
  }
  card.append(finerPanel(ctx, question));
  return card;
}

function hypothesisForm(ctx, questions, hypothesis = null) {
  if (!ctx.canWrite || !questions.length) return null;
  const form = el("form", { className: "strategy-form hypothesis-form" });
  const question = el("select", { attrs: { required: "", "aria-label": "Research question for hypothesis" } });
  questions.forEach((item) => {
    const option = el("option", { text: item.text, attrs: { value: item.id } });
    option.selected = String(item.id) === String(hypothesis?.research_question_id || "");
    question.append(option);
  });
  const statement = el("textarea", { attrs: { required: "", maxlength: "20000", "aria-label": "Hypothesis statement" } });
  const direction = el("input", { type: "text", attrs: { required: "", maxlength: "30", "aria-label": "Expected direction" } });
  const basis = el("textarea", { attrs: { required: "", maxlength: "20000", "aria-label": "Theoretical basis" } });
  const support = el("textarea", { attrs: { required: "", maxlength: "20000", "aria-label": "Empirical support" } });
  statement.value = hypothesis?.statement || "";
  direction.value = hypothesis?.direction || "";
  basis.value = hypothesis?.theoretical_basis || "";
  support.value = hypothesis?.empirical_support || "";
  const save = el("button", { type: "submit", text: hypothesis ? "Save hypothesis" : "Add hypothesis" });
  form.append(
    field("Research question", question), field("Hypothesis", statement), field("Expected direction", direction),
    field("Theoretical basis", basis), field("Empirical support", support), save,
  );
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    save.disabled = true;
    const payload = hypothesisPayload({
      researchQuestionId: question.value, statement: statement.value, direction: direction.value,
      theoreticalBasis: basis.value, empiricalSupport: support.value,
    });
    try {
      await saveAndRefresh(ctx, hypothesis ? `/hypotheses/${hypothesis.id}` : "/hypotheses", hypothesis ? "PATCH" : "POST", payload);
    } catch (error) {
      toast(error.message);
    } finally {
      save.disabled = false;
    }
  });
  return form;
}

function hypothesisCard(ctx, hypothesis, questions) {
  const card = el("article", { className: "hypothesis-card" });
  const question = questions.find((item) => String(item.id) === String(hypothesis.research_question_id));
  card.append(el("h3", { text: hypothesis.statement }), el("p", { className: "muted small", text: `Direction: ${hypothesis.direction} · Research question: ${question?.text || "Question unavailable"}` }));
  card.append(el("p", { text: `Theoretical basis: ${hypothesis.theoretical_basis}` }), el("p", { text: `Empirical support: ${hypothesis.empirical_support}` }));
  if (ctx.canWrite) {
    const details = el("details", { className: "strategy-edit-details" });
    details.append(el("summary", { text: "Edit hypothesis" }), hypothesisForm(ctx, questions, hypothesis));
    card.append(details);
  }
  return card;
}

async function render(root, ctx) {
  const intro = el("div", { className: "panel" });
  intro.append(
    el("h2", { text: "Gaps & questions" }),
    el("p", { text: "Curate evidence-backed gaps, connect them to human-authored research questions, and review FINER criteria. The critique is advisory; it does not approve a gate or replace scholarly judgment." }),
    el("p", { className: "muted small", text: "G5 and G6 remain separate human approval gates." }),
  );
  const work = [intro];
  const [gapResult, questionResult, hypothesisResult, sourceResult] = await Promise.allSettled([
    request(projectApi(ctx.project.id, "/gaps")),
    request(projectApi(ctx.project.id, "/research-questions")),
    request(projectApi(ctx.project.id, "/hypotheses")),
    request(projectApi(ctx.project.id, "/sources")),
  ]);
  const gaps = gapResult.status === "fulfilled" ? gapResult.value : [];
  const questions = questionResult.status === "fulfilled" ? questionResult.value : [];
  const hypotheses = hypothesisResult.status === "fulfilled" ? hypothesisResult.value : [];
  const sources = sourceResult.status === "fulfilled" ? sourceResult.value : [];

  const gapSection = el("section", { className: "panel" });
  gapSection.append(el("h2", { text: "Literature gaps" }));
  if (gapResult.status === "rejected") gapSection.append(errorMessage(gapResult.reason));
  else if (!gaps.length) gapSection.append(emptyCard("No gaps recorded", "Start with gaps supported by evidence, not impressions."));
  else {
    const list = el("div", { className: "strategy-list" });
    gaps.forEach((gap) => list.append(gapCard(ctx, gap, sources)));
    gapSection.append(list);
  }
  if (sourceResult.status === "rejected") gapSection.append(errorMessage(sourceResult.reason));
  else {
    const addGap = createGapForm(ctx, sources);
    if (addGap) gapSection.append(addGap);
  }
  work.push(gapSection);

  const questionSection = el("section", { className: "panel" });
  questionSection.append(el("h2", { text: "Research questions" }));
  questionSection.append(el("p", { className: "muted small", text: "Research questions are written and owned by the project team. Link selected gaps when they support the question." }));
  if (questionResult.status === "rejected") questionSection.append(errorMessage(questionResult.reason));
  else if (!questions.length) questionSection.append(emptyCard("No research questions yet", "Draft a question, then use FINER as an advisory review."));
  else {
    const list = el("div", { className: "strategy-list" });
    questions.forEach((question) => list.append(questionCard(ctx, question, gaps)));
    questionSection.append(list);
  }
  if (ctx.canWrite) questionSection.append(questionForm(ctx, gaps));
  work.push(questionSection);

  const hypothesisSection = el("section", { className: "panel" });
  hypothesisSection.append(el("h2", { text: "Hypotheses" }));
  if (hypothesisResult.status === "rejected") hypothesisSection.append(errorMessage(hypothesisResult.reason));
  else if (!hypotheses.length) hypothesisSection.append(emptyCard("No hypotheses recorded", "When appropriate, connect a human-authored hypothesis to a research question, theory, and prior evidence."));
  else {
    const list = el("div", { className: "strategy-list" });
    hypotheses.forEach((hypothesis) => list.append(hypothesisCard(ctx, hypothesis, questions)));
    hypothesisSection.append(list);
  }
  if (ctx.canWrite) {
    const form = hypothesisForm(ctx, questions);
    if (form) hypothesisSection.append(form);
    else hypothesisSection.append(el("p", { className: "muted small", text: "Add a research question before recording a hypothesis." }));
  }
  work.push(hypothesisSection);
  const approvals = el("div", { className: "panel" });
  approvals.append(el("h2", { text: "Approval" }), gateCard(ctx, "G5"), gateCard(ctx, "G6"));
  work.push(approvals);
  renderFrame(root, ctx, { stepId: "gaps", work });
}

export default { render };
