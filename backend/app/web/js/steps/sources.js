import { $, el, label, toast, formatDate, emptyCard, confirmAction } from "../dom.js";
import { request, projectApi } from "../api.js";

// Selection survives re-renders within this step but resets on project switch (module state).
let selected = new Set();
let filter = "all"; // "all" | "unverified" | "verified"
let currentProjectId = null;

// Only verified sources may be cited in an answer (citation guard, plan M1.10.3), so every card
// says whether its source is verified and offers the two ways to verify it (ported from M1.13.1).
function verificationText(source) {
  const when = source.verified_at ? ` · ${formatDate(source.verified_at)}` : "";
  let text = "Not verified: the agent can't cite this source";
  if (source.metadata_verified && source.verification_method === "human") text = `Verified by a person${when}`;
  else if (source.metadata_verified) text = `Verified by an automatic check${when}`;
  if (source.verification?.verdict) text += ` · Last automatic check: ${label(source.verification.verdict)}`;
  return text;
}

function verificationActions(ctx, source) {
  const base = projectApi(ctx.project.id, `/sources/${source.id}`);
  const actions = el("div", { className: "source-actions" });
  const check = el("button", { text: "Check automatically" });
  check.addEventListener("click", async () => {
    check.disabled = true;
    try {
      await request(`${base}/check`, { method: "POST", body: "{}" });
      toast("Automatic check queued.");
      await ctx.refresh();
    } catch (error) {
      toast(error.message);
    } finally {
      check.disabled = false;
    }
  });
  const vouch = el("button", { className: "quiet-button", text: "I've checked it" });
  vouch.addEventListener("click", async () => {
    const ok = await confirmAction(
      "Confirm verification",
      "Confirm you checked this source's title, authors and year against the original.",
      "Mark verified"
    );
    if (!ok) return;
    vouch.disabled = true;
    try {
      await request(`${base}/verify`, { method: "POST", body: "{}" });
      await ctx.refresh();
    } catch (error) {
      toast(error.message);
    } finally {
      vouch.disabled = false;
    }
  });
  actions.append(check, vouch);
  return actions;
}

function openSourceDialog() {
  $("#source-form").reset();
  $("#source-dialog").showModal();
}

function openMergeDialog(ctx, sources) {
  const chosen = sources.filter((s) => selected.has(s.id));
  const dialog = $("#merge-dialog");
  const form = $("#merge-form");
  const list = $("#merge-keep-list");
  list.replaceChildren();
  chosen.forEach((source, index) => {
    const row = el("label", { className: "checkbox-label" });
    const radio = el("input", { attrs: { type: "radio", name: "keep", value: source.id } });
    if (index === 0) radio.checked = true;
    row.append(radio, el("span", { text: source.title }));
    list.append(row);
  });
  form.onsubmit = (event) => {
    event.preventDefault();
    const keep = form.elements.keep.value;
    (async () => {
      try {
        await request(projectApi(ctx.project.id, "/sources/merge"), {
          method: "POST",
          body: JSON.stringify({ source_ids: chosen.map((s) => s.id), keep }),
        });
        dialog.close();
        selected = new Set();
        toast("Sources merged.");
        await ctx.refresh();
      } catch (error) {
        toast(error.message);
      }
    })();
  };
  dialog.showModal();
}

function sourceCard(ctx, source) {
  const card = el("article", { className: "source-card" });
  const meta = [label(source.source_type), source.year].filter(Boolean).join(" · ");
  const kind = el("span", { className: "context-kind", text: meta || "Source" });
  if (source.is_automated) kind.append(el("span", { className: "unverified-tag", text: "· Auto-retrieved, unverified" }));
  card.append(kind);
  if (source.url) {
    card.append(el("a", { className: "source-title", text: source.title, href: source.url, target: "_blank", rel: "noopener noreferrer" }));
  } else {
    card.append(el("h3", { text: source.title }));
  }
  card.append(el("p", { className: "verification-status small", text: verificationText(source) }));
  if (source.evidence_excerpt) {
    card.append(el("p", { className: "evidence-excerpt", text: source.evidence_excerpt }));
    if (source.excerpt_locator) card.append(el("p", { className: "source-locator", text: `Location: ${source.excerpt_locator}` }));
  } else {
    card.append(el("p", { className: "source-locator", text: "No evidence excerpt saved yet; the agent will not use this source for factual claims." }));
  }
  if (ctx.canWrite && !(source.metadata_verified && source.verification_method === "human")) {
    card.append(verificationActions(ctx, source));
  }
  if (ctx.canWrite) {
    const row = el("label", { className: "checkbox-label" });
    const box = el("input", { attrs: { type: "checkbox" } });
    box.checked = selected.has(source.id);
    box.addEventListener("change", () => {
      if (box.checked) selected.add(source.id);
      else selected.delete(source.id);
      toolbarButtons();
    });
    row.append(box, el("span", { text: "Select for merge" }));
    card.append(row);
  }
  return card;
}

let mergeButtonRef = null;
function toolbarButtons() {
  if (mergeButtonRef) {
    mergeButtonRef.disabled = !(selected.size >= 2 && selected.size <= 20);
    mergeButtonRef.textContent = `Merge selected (${selected.size})`;
  }
}

function sourcesList(root, ctx) {
  const list = root.querySelector("#source-list") || el("div", { className: "source-list", attrs: { id: "source-list" } });
  const all = ctx.summary.sources || [];
  const filtered = all.filter((s) => {
    if (filter === "unverified") return !s.metadata_verified;
    if (filter === "verified") return s.metadata_verified;
    return true;
  });
  if (all.length === 0) {
    list.replaceChildren(emptyCard("No evidence sources yet", "Add a primary source and a short excerpt, statistic, or data note before asking the agent for a synthesis."));
  } else if (filtered.length === 0) {
    list.replaceChildren(emptyCard("No sources match this filter", "Try a different filter."));
  } else {
    list.replaceChildren(...filtered.map((source) => sourceCard(ctx, source)));
  }
  return list;
}

function render(root, ctx) {
  if (currentProjectId !== ctx.project.id) {
    selected = new Set();
    currentProjectId = ctx.project.id;
  }
  const panel = el("div", { className: "panel" });
  const title = el("div", { className: "section-title" });
  title.append(el("div", { children: [el("h2", { text: "Sources" })] }));
  const toolbar = el("div", { className: "source-actions" });
  ["all", "unverified", "verified"].forEach((key) => {
    const button = el("button", { className: "quiet-button", text: label(key), attrs: { "aria-pressed": String(filter === key) } });
    button.addEventListener("click", () => {
      filter = key;
      sourcesList(root, ctx);
    });
    toolbar.append(button);
  });
  if (ctx.canWrite) {
    const add = el("button", { text: "Add source" });
    add.addEventListener("click", openSourceDialog);
    const merge = el("button", { className: "quiet-button", text: "Merge selected (0)" });
    merge.disabled = true;
    merge.addEventListener("click", () => openMergeDialog(ctx, ctx.summary.sources || []));
    mergeButtonRef = merge;
    toolbarButtons();
    toolbar.append(add, merge);
  }
  title.append(toolbar);
  panel.append(title);
  panel.append(sourcesList(root, ctx));
  root.replaceChildren(panel);
}

function update(root, ctx) {
  if (root.querySelector("#source-list")) sourcesList(root, ctx);
}

export default { render, update };
