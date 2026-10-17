import { el, label, toast } from "../dom.js";
import { request, projectApi } from "../api.js";
import { errorCard } from "./common.js";
import { suggestionPanel } from "../suggestion.js";
import { afterDecision, authorsText, countsText, fetchEntireQueue, keyAction, runBulk, safeLink, typedReason } from "./screenLogic.js";

// Title/abstract screening (M2.3.1), built from the approved wireframe docs/wireframes/screening-queue.html.
// Every decision is a person's own: the AI suggestion is shown as a suggestion and never pre-selected.
// Keys: I include, E exclude (then 1-9 for the reason), M maybe, J next, K previous, U undo, A show/hide the
// AI suggestion, O open the source link, ? help. Bulk actions and the conflict queue are M2.3.2.
const STAGE = "title_abstract";

let keyHandler = null;

function reasonDialog(codes) {
  const dialog = el("dialog", { attrs: { "aria-labelledby": "screen-reason-title" } });
  const form = el("form", { attrs: { method: "dialog" } });
  form.append(el("h2", { text: "Exclude: pick the reason", attrs: { id: "screen-reason-title" } }));
  form.append(el("p", { className: "muted small", text: "Press a number, or click. Every exclusion needs a reason; it goes into the PRISMA counts." }));
  const list = el("div", { className: "screen-reasons" });
  const note = el("input", { attrs: { name: "note", maxlength: "2000", "aria-label": "Note (optional)", placeholder: "Note (optional, stays in the audit file)" } });
  const cancel = el("button", { type: "button", className: "quiet-button", text: "Cancel (Esc)" });
  cancel.addEventListener("click", () => dialog.close());
  form.append(list, note, el("div", { className: "dialog-actions", children: [cancel] }));
  dialog.append(form);
  document.body.append(dialog);
  const pick = (code) => {
    clearTimeout(timer); // a click during a digit wait must not let the wait pick a second reason
    dialog.dispatchEvent(new CustomEvent("reason", { detail: { code, note: note.value.trim() || null } }));
    dialog.close();
  };
  codes.forEach((criterion, index) => {
    const button = el("button", { type: "button", className: "quiet-button screen-reason", text: `${index + 1}  ${criterion.code}  ${criterion.text}` });
    button.addEventListener("click", () => pick(criterion.code));
    list.append(button);
  });
  // Digits: 1–9 pick at once; for 10 and up, type both digits (a single "1" waits briefly in case a "0" follows).
  let typed = "";
  let timer = null;
  const fire = () => {
    // A lone digit that waited (reason 1 when 10+ exist) is the reason itself once the wait runs out.
    const n = Number.parseInt(typed, 10);
    typed = "";
    if (n >= 1 && n <= codes.length) pick(codes[n - 1].code);
  };
  dialog.addEventListener("keydown", (event) => {
    if (event.target === note || event.ctrlKey || event.metaKey || event.altKey) return;
    if (!/^[0-9]$/.test(event.key)) return;
    event.preventDefault();
    clearTimeout(timer);
    typed += event.key;
    const r = typedReason(typed, codes.length);
    if (r.index !== undefined) fire();
    else if (r.wait) timer = setTimeout(fire, 800);
    else typed = ""; // the digits name no reason (e.g. "13" with 12): pick nothing, start again
  });
  dialog.addEventListener("close", () => {
    clearTimeout(timer);
    dialog.remove();
  });
  return dialog;
}

async function loadAgreement(api) {
  try {
    return await request(api(`/agreement?stage=${STAGE}`));
  } catch (error) {
    return { error: error.message };
  }
}

// Where two reviewers disagree (M2.4.2b's `conflicts`). Nobody's answer is final for such a record until an owner or
// supervisor rules on it (M2.4.3, the `ruler` argument; null for everyone else, who see the list only).
function renderConflicts(box, agreement, names, ruler) {
  box.replaceChildren(el("h2", { text: "Disagreements between reviewers" }));
  if (agreement.error) {
    box.append(el("p", { className: "muted small", text: `Not available: ${agreement.error}` }));
    return;
  }
  const kappa = agreement.kappa == null ? "not defined yet" : agreement.kappa.toFixed(2);
  box.append(
    el("p", {
      className: "muted small",
      text:
        agreement.n === 0
          ? "No record has been decided by two reviewers yet, so there is no agreement figure."
          : `Agreement on ${agreement.n} records two reviewers both decided: ${Math.round(agreement.percent_agreement * 100)}%, Cohen's kappa ${kappa}${agreement.band ? ` (${agreement.band})` : ""}.`,
    })
  );
  if (agreement.note) box.append(el("p", { className: "muted small", text: agreement.note }));
  if (!agreement.conflicts.length) {
    box.append(el("p", { text: "No disagreements." }));
    return;
  }
  const list = el("ul", { className: "screen-conflict-list" });
  agreement.conflicts.forEach((conflict) => {
    const row = el("li");
    row.append(el("strong", { text: conflict.title || "(untitled)" }));
    const decisions = el("ul");
    conflict.decisions.forEach((d) =>
      decisions.append(el("li", { className: "small", text: `${names.get(String(d.decider)) || d.decider}: ${label(d.decision)}${d.reason_code ? ` (${d.reason_code})` : ""}` }))
    );
    row.append(decisions);
    const ruling = conflict.adjudication;
    if (ruling) {
      const by = names.get(String(ruling.decider)) || ruling.decider;
      row.append(el("p", { className: "small", text: `Ruled by ${by}: ${label(ruling.decision)}${ruling.reason_code ? ` (${ruling.reason_code})` : ""}. The reviewers' own decisions above stay as they are.` }));
    }
    if (ruler) {
      if (conflict.decisions.some((d) => String(d.decider) === ruler.userId)) {
        row.append(el("p", { className: "muted small", text: "You decided this record yourself, so you can't rule on it." }));
      } else {
        const actions = el("div", { className: "screen-actions" });
        const make = (text, handler) => {
          const button = el("button", { type: "button", className: "quiet-button", text });
          button.addEventListener("click", handler);
          actions.append(button);
        };
        const prefix = ruling ? "Rule again: " : "Rule: ";
        make(`${prefix}Include`, () => ruler.rule(conflict.source_id, "include"));
        make(`${prefix}Maybe`, () => ruler.rule(conflict.source_id, "maybe"));
        make(`${prefix}Exclude…`, () => {
          if (ruler.exclusions.length === 0) {
            toast("Add an exclusion criterion on Criteria & seeds first: every exclusion needs a reason.");
            return;
          }
          const dialog = reasonDialog(ruler.exclusions);
          dialog.addEventListener("reason", (event) => ruler.rule(conflict.source_id, "exclude", event.detail.code, event.detail.note));
          dialog.showModal();
        });
        row.append(actions);
      }
    }
    list.append(row);
  });
  box.append(list);
}

async function render(root, ctx) {
  if (keyHandler) document.removeEventListener("keydown", keyHandler, true);
  keyHandler = null;
  const api = (suffix) => projectApi(ctx.project.id, `/screening${suffix}`);
  // Every page of the waiting list, not just the first 200 (M2.3.4).
  const loadQueue = () => fetchEntireQueue((offset) => request(api(`/queue?stage=${STAGE}&limit=200&offset=${offset}`)));
  let queue;
  try {
    queue = await loadQueue();
  } catch (error) {
    root.replaceChildren(errorCard(error.message, ctx.refresh));
    return;
  }
  const criteria = ctx.summary.criteria?.criteria || [];
  const exclusions = criteria.filter((c) => c.kind === "exclude");
  const state = { items: queue.items, counts: queue.counts, pending: queue.pending, index: 0, showAi: true, undoStack: [], busy: false, selected: new Set(), hiddenAi: new Set() };
  const canDecide = ctx.canWrite && !queue.locked;

  const header = el("p", { className: "screen-counts" });
  const notice = el("p", { className: "muted small screen-notice" });
  const list = el("ol", { className: "screen-list", attrs: { "aria-label": "Records to screen" } });
  const record = el("div", { className: "screen-record" });
  const actions = el("div", { className: "screen-actions" });
  const side = el("aside", { className: "screen-side" });
  const bulkCount = el("span", { className: "muted small", text: "0 selected" });
  const bulkMaybe = el("button", { type: "button", className: "quiet-button", text: "Mark Maybe" });
  const bulkExclude = el("button", { type: "button", className: "quiet-button", text: "Exclude…" });
  const bulkBar = el("div", { className: "screen-bulk", children: [bulkCount, bulkMaybe, bulkExclude] });
  bulkMaybe.addEventListener("click", () => bulkDecide("maybe"));
  bulkExclude.addEventListener("click", () => {
    if (state.selected.size === 0 || !canDecide || state.busy) return;
    if (exclusions.length === 0) {
      toast("Add an exclusion criterion on Criteria & seeds first: every exclusion needs a reason.");
      return;
    }
    const dialog = reasonDialog(exclusions);
    dialog.addEventListener("reason", (event) => bulkDecide("exclude", event.detail.code, event.detail.note));
    dialog.showModal();
  });
  const help = el("p", {
    className: "muted small",
    text: "Keys: I include · E exclude (then the reason's number; for 10 and up, type both digits) · M maybe · J next · K previous · U undo · A show/hide AI suggestion · O open the source link",
  });

  if (!ctx.canWrite) notice.textContent = "You can read the queue, but only an owner or co-author can screen.";
  else if (queue.locked) notice.textContent = "Screening is locked because G3 is approved. Reopen G3 on Approvals to change a decision.";
  else if (queue.dual_screen_enabled) notice.textContent = "Dual screening is on: other reviewers' decisions and AI suggestions stay hidden until you decide.";
  else if (queue.sort === "relevance") notice.textContent = "Ordered by predicted relevance, from your own earlier decisions. It is only an order, never a decision.";

  const current = () => state.items[state.index] || null;

  function renderList() {
    list.replaceChildren(
      ...state.items.map((item, i) => {
        const li = el("li", { className: `screen-item${i === state.index ? " active" : ""}` });
        const box = el("input", { type: "checkbox", attrs: { "aria-label": `Select: ${item.title || "untitled"}` } });
        box.checked = state.selected.has(item.source_id);
        box.disabled = !canDecide;
        box.addEventListener("change", () => {
          if (box.checked) state.selected.add(item.source_id);
          else state.selected.delete(item.source_id);
          renderBulk();
        });
        const button = el("button", { type: "button", className: "quiet-button screen-item-button", text: item.title || "(untitled)" });
        button.addEventListener("click", () => {
          state.index = i;
          renderAll();
        });
        li.append(box, button);
        return li;
      })
    );
  }

  function renderBulk() {
    bulkCount.textContent = `${state.selected.size} selected`;
    const none = !canDecide || state.busy || state.selected.size === 0;
    bulkMaybe.disabled = none;
    bulkExclude.disabled = none;
  }

  // Bulk actions (wireframe: bulk Maybe and bulk Exclude with one reason): each record still gets its own
  // decision row, sent one at a time, so the audit file is the same as if they were decided singly.
  async function bulkDecide(decision, reasonCode = null, note = null) {
    if (!canDecide || state.busy || state.selected.size === 0) return;
    state.busy = true;
    renderBulk();
    renderActions();
    const ids = [...state.selected];
    const { done, failure } = await runBulk(ids, (id) =>
      request(api("/decisions"), {
        method: "POST",
        body: JSON.stringify({ source_id: id, stage: STAGE, decision, reason_code: reasonCode, note }),
      })
    );
    done.forEach((id) => {
      state.selected.delete(id);
      state.undoStack.push({ sourceId: id });
      const at = state.items.findIndex((i) => i.source_id === id);
      if (at >= 0) state.items.splice(at, 1);
      state.pending = Math.max(0, state.pending - 1);
      state.counts = afterDecision(state.counts, decision);
    });
    if (state.index >= state.items.length) state.index = Math.max(0, state.items.length - 1);
    state.busy = false;
    toast(failure ? `${done.length} of ${ids.length} recorded, then stopped: ${failure}` : `${done.length} records: ${label(decision)} recorded.`);
    renderAll();
    afterAction();
  }

  function renderRecord() {
    const item = current();
    if (!item) {
      // "Nothing left" only when the server's own count says so; records it still counts but the screen did not
      // load would be a loading fault, and is said as one.
      record.replaceChildren(
        state.pending > 0
          ? el("h2", { text: "Some records could not be loaded" })
          : el("h2", { text: "Nothing left to screen" }),
        el("p", {
          className: "muted",
          text:
            state.pending > 0
              ? `${state.pending} records are still waiting for your decision but did not load. Reload the page to see them.`
              : "Every record at this stage has your decision. Approve G3 on Approvals when you are satisfied with the included set.",
        })
      );
      return;
    }
    const link = safeLink(item.doi);
    const meta = el("p", { className: "muted small", text: `${authorsText(item.authors)} · ${item.year ?? "year unknown"}` });
    const source = link
      ? el("a", { href: link, target: "_blank", rel: "noopener noreferrer", text: `DOI ${item.doi}` })
      : el("span", { className: "muted small", text: "No DOI recorded" });
    const found = item.found_via ? el("p", { className: "muted small", text: `Found via ${label(item.found_via.method || "another search")}` }) : null;
    record.replaceChildren(
      el("h2", { text: item.title || "(untitled)" }),
      meta,
      el("p", { children: [source] }),
      ...(found ? [found] : []),
      el("h3", { text: "Abstract" }),
      el("p", { className: "screen-abstract", text: item.abstract || "No abstract recorded for this record." })
    );
  }

  function renderActions() {
    actions.replaceChildren();
    const item = current();
    const decide = (name, text, handler, disabled = false) => {
      const button = el("button", { type: "button", className: name === "include" || name === "exclude" || name === "maybe" ? `screen-${name}` : "quiet-button", text });
      button.disabled = disabled || state.busy;
      button.addEventListener("click", handler);
      actions.append(button);
    };
    const off = !canDecide || !item;
    decide("include", "Include (I)", () => decideCurrent("include"), off);
    decide("exclude", "Exclude… (E)", openExclude, off);
    decide("maybe", "Maybe (M)", () => decideCurrent("maybe"), off);
    decide("prev", "← Previous (K)", () => step(-1), state.index <= 0);
    decide("next", "Next → (J)", () => step(1), state.index >= state.items.length - 1);
    decide("undo", "Undo (U)", undo, !canDecide || state.undoStack.length === 0);
  }

  function renderSide() {
    const box = el("section", { className: "panel screen-criteria" });
    box.append(el("h3", { text: "Criteria" }));
    if (criteria.length === 0) box.append(el("p", { className: "muted small", text: "No criteria yet. Add them on Criteria & seeds." }));
    else {
      const ul = el("ul");
      criteria.forEach((c) => ul.append(el("li", { children: [el("code", { text: c.code }), document.createTextNode(` ${c.text}`)] })));
      box.append(ul);
    }
    const ai = el("section", { className: "panel screen-ai", attrs: { "aria-live": "polite" } });
    const item = current();
    const s = item?.ai_suggestion;
    if (!item) {
      ai.append(el("h3", { text: "AI suggestion, not a decision" }), el("p", { className: "muted small", text: "—" }));
    } else if (!state.showAi) {
      ai.append(el("h3", { text: "AI suggestion, not a decision" }), el("p", { className: "muted small", text: "Hidden. Press A to show it." }));
    } else if (state.hiddenAi.has(item.source_id) || ["discarded", "locked"].includes(s?.state)) {
      ai.append(
        el("h3", { text: "AI suggestion, not a decision" }),
        el("p", { className: "muted small", text: "No active AI suggestion for this record." })
      );
    } else if (!s) {
      ai.append(
        el("h3", { text: "AI suggestion, not a decision" }),
        el("p", { className: "muted small", text: queue.dual_screen_enabled ? "Withheld until you decide." : "No AI suggestion for this record." })
      );
    } else {
      // The shared panel (X.46.4): Accept records YOUR decision, Discard sets it aside, nothing is decided until you act.
      const wanted = item.source_id;
      ai.append(el("p", { className: "muted small", text: "Loading the AI suggestion…" }));
      suggestionPanel({ ...ctx, canWrite: canDecide }, {
        targetKind: "screening",
        targetId: wanted,
        onChange: async (path, area) => {
          if (area === "/ai-locks" || /\/discard$/.test(path)) state.hiddenAi.add(wanted);
          if (/\/restore$/.test(path)) state.hiddenAi.delete(wanted);
          if (/\/(?:accept|edit-accept)$/.test(path)) state.undoStack.push({ sourceId: wanted, suggestionId: path.split("/")[1] });
          await reloadQueue();
        },
      }).then((panel) => {
        if (current()?.source_id !== wanted || !state.showAi) return;
        if (panel.querySelector(".suggestion-card")) {
          ai.replaceChildren(panel);
          return;
        }
        if (state.hiddenAi.has(wanted)) {
          ai.replaceChildren(
            el("h3", { text: "AI suggestion, not a decision" }),
            el("p", { className: "muted small", text: "No active AI suggestion for this record." })
          );
          return;
        }
        // A pre-screen row written before suggestions were stored through the panel: show it read-only.
        const conf = s.confidence == null ? "" : ` · confidence ${Math.round(s.confidence * 100)}%`;
        ai.replaceChildren(
          el("h3", { text: "AI suggestion, not a decision" }),
          el("p", { text: `${label(s.decision)}${s.reason_code ? ` (${s.reason_code})` : ""}${conf}` }),
          el("p", { className: "small", text: s.rationale || "No rationale recorded." }),
          el("p", { className: "muted small", text: "A suggestion only. It counts for nothing until you decide." })
        );
      });
    }
    const toggle = el("button", { type: "button", className: "quiet-button", text: state.showAi ? "Hide suggestion (A)" : "Show suggestion (A)" });
    toggle.addEventListener("click", () => {
      state.showAi = !state.showAi;
      renderAll();
    });
    side.replaceChildren(box, ai, toggle);
  }

  // After the panel records something (an accepted suggestion is a decision), reload the queue so the record
  // leaves it, keeping the person's place.
  // A reload asked for while a decision or undo is in flight waits for it, so the reload never lands between that
  // request and its update (its pending count and counts would then be taken away twice).
  function afterAction() {
    if (!state.reloadWanted) return;
    state.reloadWanted = false;
    reloadQueue();
  }

  async function reloadQueue() {
    if (state.busy) {
      state.reloadWanted = true;
      return;
    }
    state.busy = true;
    try {
      const fresh = await loadQueue();
      const keep = current()?.source_id;
      state.items = fresh.items;
      state.counts = fresh.counts;
      state.pending = fresh.pending;
      const at = state.items.findIndex((i) => i.source_id === keep);
      state.index = at >= 0 ? at : Math.min(state.index, Math.max(0, state.items.length - 1));
    } catch (error) {
      toast(error.message);
    }
    state.busy = false;
    renderAll();
    afterAction();
  }

  function renderAll() {
    header.textContent = countsText(state.counts, state.pending);
    renderList();
    renderBulk();
    renderRecord();
    renderActions();
    renderSide();
  }

  function step(delta) {
    const next = state.index + delta;
    if (next < 0 || next >= state.items.length) return;
    state.index = next;
    renderAll();
  }

  async function decideCurrent(decision, reasonCode = null, note = null) {
    const item = current();
    if (!item || !canDecide || state.busy) return;
    state.busy = true;
    renderActions();
    try {
      await request(api("/decisions"), {
        method: "POST",
        body: JSON.stringify({ source_id: item.source_id, stage: STAGE, decision, reason_code: reasonCode, note }),
      });
      state.undoStack.push({ sourceId: item.source_id });
      // Remove by id, and keep the person on the record they are now viewing: they may have moved on while the
      // request was in flight.
      const keep = current()?.source_id;
      const at = state.items.findIndex((i) => i.source_id === item.source_id);
      if (at >= 0) state.items.splice(at, 1);
      state.pending = Math.max(0, state.pending - 1);
      state.counts = afterDecision(state.counts, decision);
      const back = state.items.findIndex((i) => i.source_id === keep);
      state.index = back >= 0 ? back : Math.min(state.index, Math.max(0, state.items.length - 1));
      toast(`${label(decision)} recorded.`);
    } catch (error) {
      toast(error.message);
    } finally {
      state.busy = false;
      renderAll();
      afterAction();
    }
  }

  function openExclude() {
    if (!current() || !canDecide || state.busy) return;
    if (exclusions.length === 0) {
      toast("Add an exclusion criterion on Criteria & seeds first: every exclusion needs a reason.");
      return;
    }
    const dialog = reasonDialog(exclusions);
    dialog.addEventListener("reason", (event) => decideCurrent("exclude", event.detail.code, event.detail.note));
    dialog.showModal();
  }

  async function undo() {
    const entry = state.undoStack[state.undoStack.length - 1];
    const sourceId = entry?.sourceId;
    if (!sourceId || !canDecide || state.busy) return;
    state.busy = true;
    try {
      const undoPath = entry.suggestionId ? `/suggestions/${entry.suggestionId}/undo` : null;
      await request(undoPath ? projectApi(ctx.project.id, undoPath) : api("/decisions/undo"), {
        method: "POST",
        ...(undoPath ? {} : { body: JSON.stringify({ source_id: sourceId, stage: STAGE }) }),
      });
      state.undoStack.pop();
      // The undo has been recorded: the record is waiting again even if the reload below fails, so count it now.
      state.pending += 1;
      try {
        const fresh = await loadQueue();
        state.items = fresh.items;
        state.counts = fresh.counts;
        state.pending = fresh.pending;
        state.index = Math.max(0, state.items.findIndex((i) => i.source_id === sourceId));
        toast("Undone. The record is back in your queue.");
      } catch (error) {
        toast(`Undone, but the queue did not reload: ${error.message}. Reload the page to see it.`);
      }
    } catch (error) {
      toast(error.message);
    } finally {
      state.busy = false;
      renderAll();
      afterAction();
    }
  }

  const handle = (event) => {
    // #step-body outlives this screen, so tie the handler to this screen's own nodes: once another step
    // replaces them the keys must stop working.
    if (!layout.isConnected) {
      document.removeEventListener("keydown", handle, true);
      return;
    }
    const action = keyAction({
      key: event.key,
      ctrlKey: event.ctrlKey,
      metaKey: event.metaKey,
      altKey: event.altKey,
      targetTag: event.target?.tagName,
      dialogOpen: Boolean(document.querySelector("dialog[open]")),
    });
    const handlers = {
      include: () => decideCurrent("include"),
      exclude: openExclude,
      maybe: () => decideCurrent("maybe"),
      next: () => step(1),
      previous: () => step(-1),
      undo,
      "toggle-ai": () => {
        state.showAi = !state.showAi;
        renderAll();
      },
      open: () => {
        const link = safeLink(current()?.doi);
        if (link) window.open(link, "_blank", "noopener,noreferrer");
        else toast("This record has no DOI to open.");
      },
      help: () => toast(help.textContent),
    };
    const handler = action && handlers[action];
    if (!handler) return;
    event.preventDefault();
    handler();
  };
  keyHandler = handle;
  document.addEventListener("keydown", handle, true);

  const layout = el("div", { className: "screen-layout", children: [el("section", { className: "panel screen-queue", children: [bulkBar, list] }), el("section", { className: "panel", children: [record, actions] }), side] });
  const conflicts = el("section", { className: "panel screen-conflicts" });
  root.replaceChildren(header, notice, layout, conflicts, help);
  renderAll();
  const names = new Map((ctx.summary.members || []).map((m) => [String(m.user_id), m.display_name || m.email]));
  // Owners and supervisors may rule on a conflict (M2.4.3) while screening is open under dual-screen.
  const ruler =
    (ctx.role === "owner" || ctx.role === "supervisor") && queue.dual_screen_enabled && !queue.locked
      ? {
          userId: String(ctx.user?.id),
          exclusions,
          rule: async (sourceId, decision, reasonCode = null, note = null) => {
            try {
              await request(api("/decisions/adjudicate"), {
                method: "POST",
                body: JSON.stringify({ source_id: sourceId, stage: STAGE, decision, reason_code: reasonCode, note }),
              });
              toast(`Ruling recorded: ${label(decision)}. Both reviewers' decisions stay in the audit file.`);
              await ctx.refresh();
            } catch (error) {
              toast(error.message);
            }
          },
        }
      : null;
  renderConflicts(conflicts, await loadAgreement(api), names, ruler);
}

export default { render };
