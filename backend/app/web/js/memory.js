import { $, el, formatDate } from "./dom.js";

// Promoting an idea reuses the existing #context-dialog/#context-form (wired once in app.js);
// this only sets its fields and opens it, no circular import needed.
function promoteIdea(idea) {
  const form = $("#context-form");
  form.reset();
  form.elements.kind.value = "question";
  form.elements.content.value = idea.content;
  form.elements.rationale.value = `Promoted from an idea saved ${formatDate(idea.created_at)}.`;
  $("#context-dialog").showModal();
}

export function renderMemory(root, ctx) {
  const items = ctx.summary.context || [];
  const questions = items.filter((item) => item.kind === "question" || item.kind === "objective");
  const ideas = items.filter((item) => item.kind === "idea").slice().reverse(); // newest first
  const other = items.filter((item) => !["question", "objective", "idea"].includes(item.kind));
  const nonIdeaUsed = items.filter((item) => item.kind !== "idea");
  const leftOut = nonIdeaUsed.filter((item) => item.used_by_agent === false).length;

  const sections = [];

  const questionSection = el("div", { className: "memory-section" });
  questionSection.append(el("h3", { text: "Research question" }));
  if (questions.length === 0) {
    const empty = el("p", { className: "muted small", text: "No research question yet." });
    const link = el("a", { href: `#/p/${ctx.project.id}/scope`, text: "Go to Scope" });
    questionSection.append(empty, link);
  } else {
    questionSection.append(...questions.map((item) => el("p", { className: "memory-line", text: item.content })));
  }
  sections.push(questionSection);

  const ideaSection = el("div", { className: "memory-section" });
  ideaSection.append(el("h3", { text: `Ideas (${ideas.length})` }));
  if (ideas.length === 0) {
    ideaSection.append(el("p", { className: "muted small", text: "No ideas captured yet." }));
  } else {
    ideas.forEach((idea) => {
      const card = el("div", { className: "idea-card" });
      card.append(el("p", { className: "memory-line", text: idea.content }));
      card.append(el("p", { className: "muted small", text: formatDate(idea.created_at) }));
      if (ctx.canWrite) {
        const promote = el("button", { className: "quiet-button", text: "Promote" });
        promote.addEventListener("click", () => promoteIdea(idea));
        card.append(promote);
      }
      ideaSection.append(card);
    });
  }
  sections.push(ideaSection);

  if (other.length > 0) {
    const otherSection = el("div", { className: "memory-section" });
    const link = el("a", { href: `#/p/${ctx.project.id}/scope`, text: `${other.length} more item${other.length === 1 ? "" : "s"} · Open Scope` });
    otherSection.append(link);
    sections.push(otherSection);
  }

  if (leftOut > 0) {
    sections.push(
      el("p", {
        className: "context-limit",
        text: `The agent uses ${nonIdeaUsed.length - leftOut} of ${nonIdeaUsed.length} items (ideas are never sent).`,
      })
    );
  }

  root.replaceChildren(...sections);
}
