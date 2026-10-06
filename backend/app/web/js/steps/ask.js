import { $, el, label, toast, formatDate, emptyCard } from "../dom.js";
import { request, projectApi } from "../api.js";

function renderResearchRun(run) {
  const card = el("article", { className: `run-card status-${run.status}` });
  const header = el("div", { className: "run-heading" });
  header.append(el("span", { className: "run-status", text: label(run.status) }), el("time", { text: formatDate(run.created_at) }));
  card.append(header, el("h3", { text: run.question }));
  if (run.use_web_retrieval) card.append(el("p", { className: "source-locator", text: "Web/scholar retrieval was requested for this run." }));
  if (Array.isArray(run.research_plan) && run.research_plan.length) {
    const plan = el("ol", { className: "research-plan" });
    run.research_plan.forEach((step) => plan.append(el("li", { text: step })));
    card.append(plan);
  }
  if (run.answer) card.append(el("p", { className: "run-answer", text: run.answer }));
  if (run.error_message) card.append(el("p", { className: "run-error", text: run.error_message }));
  const provenance = [
    run.provider_model ? `Model ${run.provider_model}` : null,
    run.prompt_version ? `Prompt ${run.prompt_version}` : null,
    run.started_at ? `Started ${formatDate(run.started_at)}` : null,
    run.completed_at ? `Completed ${formatDate(run.completed_at)}` : null,
  ].filter(Boolean);
  if (provenance.length) card.append(el("p", { className: "source-locator", text: provenance.join(" · ") }));
  return card;
}

function runsList(root, ctx) {
  const list = root.querySelector("#research-runs") || el("div", { className: "research-runs", attrs: { id: "research-runs" } });
  const runs = ctx.summary.runs || [];
  if (!runs.length) {
    list.replaceChildren(emptyCard("No research runs yet", "Ask a focused question after saving evidence notes. The run will retain the source snapshot used for its response."));
  } else {
    list.replaceChildren(...runs.map(renderResearchRun));
  }
  return list;
}

function render(root, ctx) {
  const panel = el("div", { className: "panel" });
  panel.append(el("h2", { text: "Ask a research question" }));
  panel.append(
    el("p", {
      className: "muted",
      text: "The agent uses this project's saved context and sources. Answers may cite only verified sources; a run that cites an unverified source fails.",
    })
  );
  if (ctx.canWrite) {
    const form = el("form", { className: "research-form" });
    const questionLabel = el("label", { attrs: { for: "ask-question" } });
    questionLabel.append(
      document.createTextNode("Question"),
      el("textarea", {
        attrs: { id: "ask-question", name: "question", required: "", minlength: "8", maxlength: "4000", placeholder: "What does the evidence say about…?" },
      })
    );
    form.append(questionLabel);
    const checkboxLabel = el("label", { className: "checkbox-label" });
    const checkbox = el("input", { attrs: { type: "checkbox", name: "use_web_retrieval" } });
    checkboxLabel.append(checkbox, document.createTextNode(" Use automated web/scholar retrieval for this run "), el("span", { className: "optional", text: "Adds unverified sources to this project" }));
    form.append(checkboxLabel);
    const actions = el("div", { className: "form-actions" });
    actions.append(
      el("p", { className: "helper", text: "Research can take a moment. You can safely keep working while a run is in progress." }),
      el("button", { type: "submit", text: "Run research" })
    );
    form.append(actions);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = form.querySelector('button[type="submit"]');
      const question = form.elements.question.value.trim();
      const useWebRetrieval = checkbox.checked;
      button.disabled = true;
      button.textContent = "Starting…";
      try {
        await request(projectApi(ctx.project.id, "/research-runs"), {
          method: "POST",
          body: JSON.stringify({ question, use_web_retrieval: useWebRetrieval }),
        });
        form.reset();
        toast("Research run started.");
        await ctx.refresh();
      } catch (error) {
        toast(error.message);
      } finally {
        button.disabled = false;
        button.textContent = "Run research";
      }
    });
    panel.append(form);
  }
  const runsHeading = el("div", { className: "runs-heading" });
  runsHeading.append(el("div", { children: [el("p", { className: "eyebrow", text: "RUN HISTORY" }), el("h3", { text: "Research runs" })] }));
  panel.append(runsHeading);
  panel.append(runsList(root, ctx));
  root.replaceChildren(panel);
}

function update(root, ctx) {
  if (root.querySelector("#research-runs")) runsList(root, ctx);
}

export default { render, update };
