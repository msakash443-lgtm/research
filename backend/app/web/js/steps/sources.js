import { $, el, label, toast, formatDate, emptyCard, confirmAction } from "../dom.js";
import { request, projectApi } from "../api.js";
import { actorName, provenanceBadge } from "../provenance.js";

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

// Open-access full text (plan M2.7.1/M2.7.7). The server stores a PDF only when its licence permits;
// otherwise it keeps the link, and the card says which happened and why.
function fulltextText(source) {
  const access = source.fulltext_access;
  if (!access) return null;
  const licence = access.licence || "unknown";
  if (access.status === "stored") return `Full text stored · licence ${licence}`;
  if (access.status === "link_only") return `Open-access copy found, but its licence (${licence}) doesn't allow storing a copy: link only`;
  if (access.status === "no_pdf") return "Open-access page found, but no PDF link";
  if (access.status === "no_oa") return "No open-access copy found";
  return null;
}

function fulltextFetchButton(ctx, source) {
  const button = el("button", { className: "quiet-button", text: source.fulltext_access ? "Look for open-access PDF again" : "Fetch open-access PDF" });
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      await request(projectApi(ctx.project.id, `/sources/${source.id}/fulltext/fetch`), { method: "POST", body: "{}" });
      toast("Full-text fetch queued. A PDF is stored only if its licence allows it.");
      await ctx.refresh();
    } catch (error) {
      toast(error.message);
    } finally {
      button.disabled = false;
    }
  });
  return button;
}

function fulltextSection(ctx, source) {
  const section = el("div", { className: "source-fulltext" });
  const text = fulltextText(source);
  if (text) {
    const when = source.fulltext_access.checked_at ? ` · checked ${formatDate(source.fulltext_access.checked_at)}` : "";
    section.append(el("p", { className: "source-locator", text: text + when }));
  }
  if (source.oa_url && /^https?:\/\//i.test(source.oa_url)) {
    section.append(el("a", { className: "small", text: "Open-access copy", href: source.oa_url, target: "_blank", rel: "noopener noreferrer" }));
  }
  if (ctx.canWrite && source.doi && !source.fulltext_path) {
    section.append(el("div", { className: "source-actions", children: [fulltextFetchButton(ctx, source)] }));
  }
  return section.childElementCount ? section : null;
}

// How snowballing found a source (M1.9.2). Titles are third-party text, so they go in as text only.
function foundViaText(via) {
  if (!via || via.method !== "snowball") return "";
  const from = (via.via && via.via.title) || "an earlier paper";
  const how = via.direction === "forward" ? "cites" : "is cited by";
  return `Found via snowballing (round ${via.round}): it ${how} “${from}”`;
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
  const via = foundViaText(source.found_via);
  if (via) card.append(el("p", { className: "source-locator", text: via }));
  // Agent-retrieved sources carry a provenance badge (X.17); retrieval uses no language model, and the
  // human approval for a source is its verification.
  if (source.origin === "retrieved") {
    const byPerson = source.metadata_verified && source.verification_method === "human";
    card.append(
      provenanceBadge({
        producer: actorName(ctx, source.created_by),
        date: source.created_at,
        approval: byPerson
          ? { state: "approved", text: `Verified by a person${source.verified_at ? ` · ${formatDate(source.verified_at)}` : ""}` }
          : { state: "pending", text: source.metadata_verified ? "Checked automatically, not yet by a person" : "Not verified" },
      })
    );
  }
  if (source.evidence_excerpt) {
    card.append(el("p", { className: "evidence-excerpt", text: source.evidence_excerpt }));
    if (source.excerpt_locator) card.append(el("p", { className: "source-locator", text: `Location: ${source.excerpt_locator}` }));
  } else {
    card.append(el("p", { className: "source-locator", text: "No evidence excerpt saved yet; the agent will not use this source for factual claims." }));
  }
  const fulltext = fulltextSection(ctx, source);
  if (fulltext) card.append(fulltext);
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
