import { $, el, label, toast, formatDate, emptyCard } from "../dom.js";
import { request, projectApi } from "../api.js";
import { actorName, provenanceBadge } from "../provenance.js";

const AGENT_RESEARCH_RUN = "agent:research-run"; // app/audit.py AGENT_RESEARCH_RUN

function renderResearchRun(ctx, run) {
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
  if (run.insufficient_evidence) card.append(el("p", { className: "run-answer", text: `The model found the evidence insufficient: ${run.insufficient_reason || "no reason given"}` }));
  if (run.error_message) card.append(el("p", { className: "run-error", text: run.error_message }));
  card.append(
    provenanceBadge({
      producer: actorName(ctx, AGENT_RESEARCH_RUN),
      requestedBy: actorName(ctx, run.created_by),
      model: run.provider_model,
      promptVersion: run.prompt_version,
      date: run.completed_at || run.created_at,
      approval: runApproval(ctx, run),
    })
  );
  const timing = [
    typeof run.confidence === "number" ? `Model confidence ${Math.round(run.confidence * 100)}%` : null,
    run.started_at ? `Started ${formatDate(run.started_at)}` : null,
    run.completed_at ? `Completed ${formatDate(run.completed_at)}` : null,
  ].filter(Boolean);
  if (timing.length) card.append(el("p", { className: "source-locator", text: timing.join(" · ") }));
  return card;
}

// Research answers have no approval step of their own yet, so the badge says so plainly rather than
// implying one; a re-entry that made the answer stale (M0.5.4) is shown as well.
function runApproval(ctx, run) {
  const stale = (ctx.summary.staleArtifacts || []).find((a) => a.kind === "research_run" && a.ref_id === run.id);
  if (stale) return { state: "stale", text: `Stale: ${stale.stale_reason || "an earlier stage was reopened"}. Re-run before relying on it.` };
  if (run.status !== "completed") return { state: "pending", text: "Nothing to approve: this run produced no answer." };
  return { state: "pending", text: "Not approved by a person. Research answers have no approval step yet; check the cited sources." };
}

function runsList(root, ctx) {
  const list = root.querySelector("#research-runs") || el("div", { className: "research-runs", attrs: { id: "research-runs" } });
  const runs = ctx.summary.runs || [];
  if (!runs.length) {
    list.replaceChildren(emptyCard("No research runs yet", "Ask a focused question after saving evidence notes. The run will retain the source snapshot used for its response."));
  } else {
    list.replaceChildren(...runs.map((run) => renderResearchRun(ctx, run)));
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
