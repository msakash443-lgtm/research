// Provenance badge for AI/agent output (plan X.17, spec §13.4): who or what produced it, who asked for it,
// which model and prompt version, when, and whether a person has approved it. Every field is shown even
// when empty ("not recorded"), so a missing value is visible instead of silently dropped.
import { el, formatDate } from "./dom.js";

const AGENT_NAMES = {
  "agent:research-run": "AI agent (research run)",
  "agent:arc-retrieval": "Retrieval agent (ARC web/scholar search)",
  "agent:worker": "Background worker",
};

export function actorName(ctx, actor) {
  if (!actor) return "Not recorded";
  if (AGENT_NAMES[actor]) return AGENT_NAMES[actor];
  if (actor.startsWith("agent:")) return `Agent (${actor.slice("agent:".length)})`;
  const member = (ctx.summary.members || []).find((m) => m.user_id === actor);
  return member ? member.display_name || member.email : "A user who is no longer a member";
}

// approval: { text, state } where state is "approved" | "pending" | "stale" (drives the colour only).
export function provenanceBadge({ producer, requestedBy, model, promptVersion, date, approval }) {
  const list = el("dl", { className: "provenance-badge" });
  const row = (term, value, className) => {
    list.append(el("dt", { text: term }), el("dd", { className: className || "", text: value || "Not recorded" }));
  };
  row("Produced by", producer);
  if (requestedBy !== undefined) row("Requested by", requestedBy);
  if (model !== undefined) row("Model", model);
  if (promptVersion !== undefined) row("Prompt", promptVersion);
  row("Date", formatDate(date));
  row("Approval", approval.text, `approval approval-${approval.state}`);
  return list;
}
