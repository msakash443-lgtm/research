import { el } from "../dom.js";
import { request, projectApi } from "../api.js";
import { authorsText, safeLink } from "./screenLogic.js";

// The context a person needs before deciding G3 (X.215): the included set with each source's evidence,
// shown on Approvals beside the Approve button. Read-only; approving stays the gate card's explicit action.

export function reviewSummary(data) {
  const count = data.items.length;
  const parts = [`${count} ${count === 1 ? "record is" : "records are"} included at full text.`];
  if (data.awaiting_full_text > 0) {
    parts.push(`${data.awaiting_full_text} of ${data.passed_title_abstract} that passed title/abstract still ${data.awaiting_full_text === 1 ? "has" : "have"} no full-text decision.`);
  }
  if (data.locked) parts.push("G3 is approved; reopen it to change a screening decision.");
  return parts.join(" ");
}

function sourceRow(item) {
  const row = el("li", { className: "review-source" });
  const details = el("details");
  const year = item.year ? ` (${item.year})` : "";
  details.append(el("summary", { text: `${item.title}${year}` }));
  details.append(
    el("p", { className: "muted small", text: authorsText(item.authors) }),
    el("p", { className: "muted small", text: item.metadata_verified ? "Metadata verified" : "Metadata not verified" })
  );
  details.append(el("p", { text: item.abstract || "No abstract recorded." }));
  const doi = safeLink(item.doi);
  if (doi) details.append(el("a", { href: doi, text: "Open the paper (DOI)", target: "_blank", rel: "noopener noreferrer" }));
  row.append(details);
  return row;
}

export function reviewNode(ctx, data) {
  const panel = el("div", { className: "panel screening-review", attrs: { id: "screening-approval" } });
  panel.append(
    el("h2", { text: "Review screening approval (G3)" }),
    el("p", { className: "muted small", text: "Inspect the included set before you decide. Approving stays your decision, made with the G3 button below." }),
    el("p", { text: reviewSummary(data) })
  );
  if (data.items.length) {
    const list = el("ul", { className: "warning-list" });
    data.items.forEach((item) => list.append(sourceRow(item)));
    panel.append(list);
  }
  const base = `#/p/${ctx.project.id}`;
  panel.append(
    el("a", { className: "back-link", href: `${base}/screen`, text: "Open Screening" }),
    el("span", { text: " · " }),
    el("a", { className: "back-link", href: `${base}/overview`, text: "Back to Overview" })
  );
  return panel;
}

export async function screeningReviewPanel(ctx) {
  if (!(ctx.summary.gates || []).some((gate) => gate.code === "G3")) return null;
  try {
    return reviewNode(ctx, await request(projectApi(ctx.project.id, "/screening/included")));
  } catch (error) {
    const panel = el("div", { className: "panel screening-review" });
    panel.append(el("h2", { text: "Review screening approval (G3)" }), el("p", { className: "muted small", text: error.message }));
    return panel;
  }
}
