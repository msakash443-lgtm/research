// The shared AI suggestion panel (plan X.46.4, API in app/routers/suggestions.py). Every screen that shows AI
// output uses this, so a person always sees the same thing: the AI's proposal is "not yet yours" until they
// accept it, and every action is recorded with who did it, the model and the prompt version.
//
//   const panel = await suggestionPanel(ctx, { targetKind: "note", targetId: note.id, onChange });
//
// Pass the result as `ai` to renderFrame. Read-only roles see the suggestions and their history but no buttons.
// All text is inserted with textContent, never as HTML: a suggestion is untrusted model output.
import { el, formatDate, toast, confirmAction } from "./dom.js";
import { request, projectApi } from "./api.js";
import { actorName, provenanceBadge } from "./provenance.js";

export const PANEL_TITLE = "AI suggestion, not yet yours";

function approvalFor(ctx, suggestion) {
  if (suggestion.state === "accepted") {
    return { state: "approved", text: `Accepted by ${actorName(ctx, suggestion.accepted_by)} on ${formatDate(suggestion.accepted_at)}` };
  }
  if (suggestion.state === "discarded") return { state: "stale", text: "Discarded by a person" };
  if (suggestion.state === "superseded") return { state: "stale", text: "Replaced by a newer answer" };
  if (suggestion.state === "locked") return { state: "stale", text: "Never for this item" };
  return { state: "pending", text: "Not yet yours: waiting for your decision" };
}

function evidenceLine(item) {
  if (typeof item === "string") return item;
  if (item && typeof item === "object") {
    const where = item.source_id || item.source || item.id;
    const quote = item.quote || item.text;
    if (where && quote) return `${where}: ${quote}`;
    if (where) return String(where);
  }
  return JSON.stringify(item);
}

// `extraActions(suggestion)` may return extra buttons for a card (the writing screen adds Insert for accepted wording).
export async function suggestionPanel(ctx, { targetKind, targetId, onChange, extraActions } = {}) {
  const panel = el("div", { className: "panel suggestion-panel" });
  let showDiscarded = false;
  let busy = false;

  const query = () => {
    const params = new URLSearchParams();
    if (targetKind) params.set("target_kind", targetKind);
    if (targetId) params.set("target_id", targetId);
    if (showDiscarded) params.set("show_discarded", "true");
    return projectApi(ctx.project.id, `/suggestions?${params}`);
  };

  // Run one action, then redraw. A refusal from the server is shown as the server worded it, and the panel is left
  // as it was, so wording or guidance the person typed is not lost. While a request is out every button is off, so
  // a double click cannot send the same action twice.
  async function act(path, options, area = "/suggestions") {
    if (busy) return;
    busy = true;
    const buttons = Array.from(panel.querySelectorAll("button"));
    buttons.forEach((node) => (node.disabled = true));
    try {
      await request(projectApi(ctx.project.id, `${area}${path}`), options);
    } catch (error) {
      toast(error.message);
      buttons.forEach((node) => (node.disabled = false));
      busy = false;
      return;
    }
    busy = false;
    await load();
    if (onChange) await onChange();
  }
  const post = (path, body) => act(path, { method: "POST", ...(body ? { body: JSON.stringify(body) } : {}) });

  function button(text, handler, quiet = true) {
    const node = el("button", { className: quiet ? "quiet-button" : "", type: "button", text });
    node.addEventListener("click", handler);
    return node;
  }

  function card(suggestion) {
    const node = el("article", { className: `suggestion-card suggestion-${suggestion.state}` });
    const text = suggestion.state === "accepted" ? suggestion.accepted_text : suggestion.content;
    node.append(el("p", { className: "suggestion-text", text }));
    if (suggestion.state === "accepted" && suggestion.accepted_text !== suggestion.content) {
      node.append(el("p", { className: "muted small", text: `The AI proposed: ${suggestion.content}` }));
    }
    if (suggestion.confidence !== null) {
      node.append(el("p", { className: "muted small", text: `AI confidence ${Math.round(suggestion.confidence * 100)}%` }));
    }
    if (suggestion.guidance) node.append(el("p", { className: "muted small", text: `Asked again with: ${suggestion.guidance}` }));
    node.append(
      provenanceBadge({
        producer: actorName(ctx, `agent:${suggestion.producing_agent}`),
        model: suggestion.model_id,
        promptVersion: suggestion.prompt_version,
        date: suggestion.created_at,
        approval: approvalFor(ctx, suggestion),
      })
    );

    const details = el("div", { className: "suggestion-details" });
    const actions = el("div", { className: "suggestion-actions" });
    const id = suggestion.id;

    const why = button("Why?", async () => {
      if (!details.hidden && details.dataset.mode === "why") {
        details.hidden = true;
        return;
      }
      try {
        const data = await request(projectApi(ctx.project.id, `/suggestions/${id}/why`));
        const list = el("ul", { className: "suggestion-evidence" });
        data.evidence.forEach((item) => list.append(el("li", { text: evidenceLine(item) })));
        details.replaceChildren(
          el("p", { className: "small", text: data.evidence.length ? "What it was based on:" : "The AI gave no evidence for this." }),
          list
        );
        details.dataset.mode = "why";
        details.hidden = false;
      } catch (error) {
        toast(error.message);
      }
    });
    actions.append(why);

    if (ctx.canWrite && suggestion.state === "pending") {
      const editor = (mode, build) => () => {
        if (!details.hidden && details.dataset.mode === mode) {
          details.hidden = true;
          return;
        }
        details.replaceChildren(...build());
        details.dataset.mode = mode;
        details.hidden = false;
      };
      const editBox = () => {
        const area = el("textarea", { attrs: { rows: "4", "aria-label": "Your wording" } });
        area.value = suggestion.content;
        return [
          area,
          button("Save my wording and accept", () => {
            if (area.value.trim()) post(`/${id}/edit-accept`, { text: area.value });
          }, false),
        ];
      };
      const askBox = () => {
        const input = el("input", { attrs: { type: "text", maxlength: "1000", placeholder: "What should be different?", "aria-label": "Guidance" } });
        return [
          input,
          button("Ask again", () => {
            if (input.value.trim()) post(`/${id}/ask-again`, { guidance: input.value });
          }, false),
        ];
      };
      actions.prepend(
        button("Accept", () => post(`/${id}/accept`), false),
        button("Edit then accept", editor("edit", editBox)),
        button("Discard", () => post(`/${id}/discard`)),
        button("Ask again", editor("ask", askBox))
      );
    }
    if (ctx.canWrite && suggestion.state === "accepted") actions.prepend(button("Undo", () => post(`/${id}/undo`)));
    if (ctx.canWrite && suggestion.state === "discarded") actions.prepend(button("Restore", () => post(`/${id}/restore`)));
    // "Never for this item" (X.46.3): lifts the item out of every AI step, not just this suggestion. Asks first, because
    // it also closes this item's other open suggestions.
    const lockPath = `/${encodeURIComponent(suggestion.target_kind)}/${encodeURIComponent(suggestion.target_id)}`;
    if (ctx.canWrite && (suggestion.state === "pending" || suggestion.state === "discarded")) {
      actions.append(
        button("Never for this item", async () => {
          const ok = await confirmAction(
            "Never use this item with AI?",
            "No AI step will be given this item again, and its open suggestions are closed. You can allow it again later.",
            "Never for this item"
          );
          if (ok) await act(lockPath, { method: "PUT" }, "/ai-locks");
        })
      );
    }
    if (ctx.canWrite && suggestion.state === "locked") {
      actions.append(button("Allow AI for this item again", () => act(lockPath, { method: "DELETE" }, "/ai-locks")));
    }

    if (extraActions) actions.append(...extraActions(suggestion));

    details.hidden = true;
    node.append(actions, details);
    return node;
  }

  function bulkBar(pending) {
    const scored = pending.filter((s) => s.confidence !== null);
    if (!ctx.canWrite || scored.length < 2) return null;
    const bar = el("div", { className: "suggestion-bulk" });
    const number = (name, value) =>
      el("input", { attrs: { type: "number", min: "0", max: "100", step: "5", value: String(value), "aria-label": name } });
    const high = number("Accept at or above (percent)", 90);
    const low = number("Discard below (percent)", 30);
    const apply = button("Apply to all", async () => {
      const accept = Number(high.value) / 100;
      const discard = Number(low.value) / 100;
      if (!(accept >= 0 && accept <= 1 && discard >= 0 && discard <= 1) || discard > accept) {
        toast("Use percentages from 0 to 100, with the discard line not above the accept line.");
        return;
      }
      const toAccept = scored.filter((s) => s.confidence >= accept).length;
      const toDiscard = scored.filter((s) => s.confidence < discard).length;
      const ok = await confirmAction(
        "Decide in bulk?",
        `Accept ${toAccept} suggestion(s) at ${high.value}% confidence or more and discard ${toDiscard} below ${low.value}%. Suggestions with no confidence are left for you.`,
        "Apply"
      );
      if (!ok) return;
      await post("/bulk", {
        accept_at_or_above: accept,
        discard_below: discard,
        ...(targetKind ? { target_kind: targetKind } : {}),
        ...(targetId ? { target_id: targetId } : {}),
      });
    }, false);
    bar.append(
      el("span", { className: "small", text: "Decide many at once:" }),
      el("label", { className: "small", children: ["Accept at or above ", high, " %"] }),
      el("label", { className: "small", children: ["Discard below ", low, " %"] }),
      apply
    );
    return bar;
  }

  async function load() {
    let items;
    try {
      items = await request(query());
    } catch (error) {
      panel.replaceChildren(el("h2", { text: PANEL_TITLE }), el("p", { className: "muted small", text: error.message }));
      return;
    }
    const head = el("div", { className: "section-title" });
    head.append(el("h2", { text: PANEL_TITLE }));
    const toggle = el("label", { className: "small suggestion-toggle" });
    const box = el("input", { attrs: { type: "checkbox" } });
    box.checked = showDiscarded;
    box.addEventListener("change", () => {
      showDiscarded = box.checked;
      load();
    });
    toggle.append(box, " Show discarded");
    head.append(toggle);

    const nodes = [head];
    const bar = bulkBar(items.filter((s) => s.state === "pending"));
    if (bar) nodes.push(bar);
    if (!items.length) nodes.push(el("p", { className: "muted", text: "Nothing suggested here yet." }));
    items.forEach((suggestion) => nodes.push(card(suggestion)));
    panel.replaceChildren(...nodes);
  }

  // With the project's AI setting Off (X.31.7) no AI output is shown or asked for: the panel stays out of the page.
  if (ctx.project.ai_involvement === "off") {
    panel.hidden = true;
    return panel;
  }
  await load();
  return panel;
}
