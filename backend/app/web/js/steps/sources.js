import { $, el, label, toast, formatDate, emptyCard, confirmAction } from "../dom.js";
import { request, projectApi } from "../api.js";
import { actorName, provenanceBadge } from "../provenance.js";
import { filterByCitations, rankByCitations } from "../citationSortFilterLogic.js";
import { renderFrame } from "./frame.js";
import {
  buildBibtexOwnPaperPayload,
  buildManualOwnPaperPayload,
  buildOwnPaperLookupPayload,
  describeOwnPaperLookup,
  isLookupPending,
} from "../ownPaperLogic.js";

// Selection survives re-renders within this step but resets on project switch (module state).
let selected = new Set();
let filter = "all"; // "all" | "unverified" | "verified"
// Citation sort/filter (M2.13.2): citationSort ranks by `citation_count` (highest first, unknown
// counts last); minCitations hides known counts below it (unknown counts are never hidden, per
// citationSortFilterLogic.js). citationStatsRef is the hidden/unknown counts line, updated in place
// by sourcesList() so it stays correct across both a full render() and a toolbar-only update.
let citationSort = false;
let minCitations = null;
let citationStatsRef = null;
let currentProjectId = null;

// "Add my own paper" panel state (M2.14.2): survives re-renders, resets on project switch.
let ownPaperMode = "lookup"; // "lookup" | "manual" | "bibtex"
let ownPaperLookup = null; // { taskId, kind: "pending"|"included"|"not_found"|"failed", text } | null
let ownPaperLookupTimer = null;

function resetOwnPaperLookup() {
  if (ownPaperLookupTimer) {
    window.clearTimeout(ownPaperLookupTimer);
    ownPaperLookupTimer = null;
  }
  ownPaperLookup = null;
}


// EC-3.3 (search_planned -> retrieved, Information level) counts auto-retrieved sources not yet
// verified. Information-level conditions never appear in the frame's own condition box (X.48.1), so
// this screen shows it directly, same source as the unmet-condition list (`GET /stage`'s `next`).
function verificationHint(ctx) {
  const option = (ctx.summary.stage?.next || []).find((o) => o.stage === "retrieved");
  const condition = option?.conditions?.find((c) => c.id === "EC-3.3");
  if (!condition) return null;
  const box = el("div", { className: "panel" });
  const message = condition.message.charAt(0).toUpperCase() + condition.message.slice(1);
  box.append(el("p", { className: "muted small", text: message }));
  if (!condition.met && condition.fix) box.append(el("p", { className: "muted small", text: `Where: ${condition.fix}` }));
  return box;
}

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
  if (access.status === "lookup_failed") return "Open-access lookup failed for at least one service; try again later";
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

// Attach a PDF directly for a source with no stored full text yet (M2.14.2): mainly for own papers,
// which often have no DOI to run the open-access fetch above. Raw bytes, not multipart (matches the
// server's `.../fulltext/upload`, which reads `request.stream()`), so this bypasses `request()`'s
// default JSON content type.
function attachPdfButton(ctx, source) {
  const wrap = el("div", { className: "source-actions" });
  const file = el("input", { attrs: { type: "file", accept: "application/pdf", "aria-label": "PDF file to attach" } });
  const upload = el("button", { className: "quiet-button", text: "Attach PDF" });
  const error = el("p", { className: "own-paper-error small" });
  upload.addEventListener("click", async () => {
    error.textContent = "";
    const picked = file.files && file.files[0];
    if (!picked) {
      error.textContent = "Choose a PDF file first.";
      return;
    }
    upload.disabled = true;
    try {
      const bytes = await picked.arrayBuffer();
      await request(projectApi(ctx.project.id, `/sources/${source.id}/fulltext/upload`), {
        method: "POST",
        headers: { "Content-Type": "application/pdf" },
        body: bytes,
      });
      toast("PDF attached.");
      await ctx.refresh();
    } catch (uploadError) {
      error.textContent = uploadError.message;
    } finally {
      upload.disabled = false;
    }
  });
  wrap.append(file, upload);
  return el("div", { children: [wrap, error] });
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
  if (ctx.canWrite && !source.fulltext_path) {
    if (source.doi) section.append(el("div", { className: "source-actions", children: [fulltextFetchButton(ctx, source)] }));
    section.append(attachPdfButton(ctx, source));
  }
  if (ctx.canWrite && source.fulltext_path) section.append(highlightCapture(ctx, source));
  return section.childElementCount ? section : null;
}

function highlightCapture(ctx, source) {
  const panel = el("div", { className: "source-highlight" });
  const load = el("button", { className: "quiet-button", type: "button", text: "Open PDF text to highlight" });
  const body = el("div", { className: "source-highlight-body", attrs: { hidden: "" } });
  load.addEventListener("click", async () => {
    load.disabled = true;
    try {
      const fulltext = await request(projectApi(ctx.project.id, `/sources/${source.id}/fulltext`));
      const selection = el("pre", {
        className: "source-fulltext-selectable",
        text: fulltext.text || "",
        attrs: { tabindex: "0", "aria-label": "Stored PDF text; select a passage to save as a highlight" },
      });
      const comment = el("textarea", { attrs: { maxlength: "20000", rows: "3", "aria-label": "Comment on this highlight", placeholder: "Optional note about this passage…" } });
      const locator = el("input", { attrs: { maxlength: "255", "aria-label": "Page or section", placeholder: "Page or section (optional)" } });
      const save = el("button", { type: "button", text: "Save selected passage" });
      save.addEventListener("click", async () => {
        const quotedText = window.getSelection()?.toString().trim() || "";
        if (!quotedText) {
          toast("Select a passage in the PDF text first.");
          return;
        }
        save.disabled = true;
        try {
          await request("/api/notes/highlight", {
            method: "POST",
            body: JSON.stringify({
              client_id: crypto.randomUUID(),
              source_id: source.id,
              quoted_text: quotedText,
              body: comment.value.trim(),
              locator: locator.value.trim() || null,
            }),
          });
          toast("Highlight saved to your Inbox and source excerpts.");
          comment.value = "";
          locator.value = "";
          window.getSelection()?.removeAllRanges();
        } catch (error) {
          toast(error.message);
        } finally {
          save.disabled = false;
        }
      });
      body.replaceChildren(selection, comment, locator, save);
      body.hidden = false;
      load.hidden = true;
      selection.focus();
    } catch (error) {
      toast(error.message);
    } finally {
      load.disabled = false;
    }
  });
  panel.append(load, body);
  return panel;
}



// How snowballing found a source (M1.9.2). Titles are third-party text, so they go in as text only.
function foundViaText(via) {
  if (!via || via.method !== "snowball") return "";
  const from = (via.via && via.via.title) || "an earlier paper";
  const how = via.direction === "forward" ? "cites" : "is cited by";
  return `Found via snowballing (round ${via.round}): it ${how} “${from}”`;
}

// How many times a source has been cited, per a third-party service (M2.13); a missing count is
// "unknown", never shown as 0.
function citationText(source) {
  if (source.citation_count == null) return "Citations: unknown";
  const via = source.citation_count_source ? ` (${label(source.citation_count_source)})` : "";
  return `Citations: ${source.citation_count}${via}`;
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
  card.append(el("p", { className: "source-locator", text: citationText(source) }));
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
  const statusFiltered = all.filter((s) => {
    if (filter === "unverified") return !s.metadata_verified;
    if (filter === "verified") return s.metadata_verified;
    return true;
  });
  const ranked = citationSort
    ? rankByCitations(statusFiltered, (s) => s.citation_count, minCitations)
    : filterByCitations(statusFiltered, (s) => s.citation_count, minCitations);
  const filtered = ranked.shown.map((row) => row.item);
  if (citationStatsRef) {
    citationStatsRef.textContent = statusFiltered.length
      ? `${ranked.hiddenBelowThreshold} hidden below the citation threshold · ${ranked.unknownCount} unknown citation count`
      : "";
  }
  if (all.length === 0) {
    list.replaceChildren(emptyCard("No evidence sources yet", "Add a primary source and a short excerpt, statistic, or data note before asking the agent for a synthesis."));
  } else if (filtered.length === 0) {
    list.replaceChildren(emptyCard("No sources match this filter", "Try a different filter or citation threshold."));
  } else {
    list.replaceChildren(...filtered.map((source) => sourceCard(ctx, source)));
  }
  return list;
}

// --- "Add my own paper" panel (M2.14.2) ---------------------------------------------------------

function ownPaperErrorNode(message) {
  return el("p", { className: "own-paper-error small", text: message });
}

function ownPaperLookupStatusNode() {
  if (!ownPaperLookup) return null;
  return el("p", { className: `own-paper-lookup-status own-paper-lookup-${ownPaperLookup.kind} small`, text: ownPaperLookup.text });
}

const OWN_PAPER_LOOKUP_POLL_MS = 2500;
const OWN_PAPER_LOOKUP_MAX_ATTEMPTS = 40; // ~100s before telling the person to check back later

function pollOwnPaperLookup(ctx, taskId, statusSlot, onIncluded, attempt = 0) {
  ownPaperLookupTimer = window.setTimeout(async () => {
    try {
      const row = await request(projectApi(ctx.project.id, `/sources/own/lookup?task_id=${taskId}`));
      const outcome = describeOwnPaperLookup(row);
      ownPaperLookup = { taskId, ...outcome };
      statusSlot.replaceChildren(ownPaperLookupStatusNode());
      if (outcome.kind === "included") {
        toast("Your paper was added.");
        await onIncluded();
      }
    } catch (error) {
      if (isLookupPending(error) && attempt < OWN_PAPER_LOOKUP_MAX_ATTEMPTS) {
        pollOwnPaperLookup(ctx, taskId, statusSlot, onIncluded, attempt + 1);
        return;
      }
      ownPaperLookup = {
        taskId, kind: "failed",
        text: isLookupPending(error) ? "Still looking after a while; check back later." : error.message,
      };
      statusSlot.replaceChildren(ownPaperLookupStatusNode());
    }
  }, OWN_PAPER_LOOKUP_POLL_MS);
}

function ownPaperLookupForm(ctx, errorSlot, statusSlot, onSaved) {
  const form = el("div");
  const doiInput = el("input", { attrs: { placeholder: "DOI, e.g. 10.1234/example", maxlength: "300", "aria-label": "DOI" } });
  const titleInput = el("input", { attrs: { placeholder: "Exact title", maxlength: "500", "aria-label": "Title" } });
  const grid = el("div", { className: "form-grid" });
  grid.append(
    el("label", { children: [document.createTextNode("DOI"), doiInput] }),
    el("label", { children: [document.createTextNode("or title"), titleInput] })
  );
  const submit = el("button", { text: "Look it up" });
  submit.addEventListener("click", async () => {
    errorSlot.replaceChildren();
    let payload;
    try {
      payload = buildOwnPaperLookupPayload({ doi: doiInput.value, title: titleInput.value });
    } catch (error) {
      errorSlot.replaceChildren(ownPaperErrorNode(error.message));
      return;
    }
    resetOwnPaperLookup();
    submit.disabled = true;
    try {
      const queued = await request(projectApi(ctx.project.id, "/sources/own/lookup"), { method: "POST", body: JSON.stringify(payload) });
      ownPaperLookup = { taskId: queued.task_id, ...describeOwnPaperLookup(null) };
      statusSlot.replaceChildren(ownPaperLookupStatusNode());
      pollOwnPaperLookup(ctx, queued.task_id, statusSlot, onSaved);
    } catch (error) {
      errorSlot.replaceChildren(ownPaperErrorNode(error.message));
    } finally {
      submit.disabled = false;
    }
  });
  form.append(grid, el("div", { className: "form-actions", children: [submit] }));
  return form;
}

function ownPaperManualForm(ctx, errorSlot, onSaved) {
  const form = el("form", { className: "form-grid" });
  const input = (name, placeholder, attrs = {}) => el("input", { attrs: { name, placeholder, ...attrs } });
  const field = (className, labelText, optional, control) => {
    const children = [document.createTextNode(labelText)];
    if (optional) children.push(document.createTextNode(" "), el("span", { className: "optional", text: "Optional" }));
    children.push(control);
    return el("label", { className, children });
  };
  const typeSelect = el("select", { attrs: { name: "source_type" } });
  ["article", "dataset", "report", "webpage", "book", "other"].forEach((value) => {
    typeSelect.append(el("option", { attrs: { value }, text: label(value) }));
  });
  form.append(
    field("full-width", "Title", false, input("title", "Paper title", { required: "", maxlength: "500" })),
    field("full-width", "Authors", true, input("authors", "Jane Doe, John Smith")),
    field("", "Year", true, input("year", "2024", { type: "number", min: "1000", max: "3000" })),
    field("", "DOI", true, input("doi", "10.1234/example", { maxlength: "300" })),
    field("", "URL", true, input("url", "https://…", { type: "url" })),
    field("", "Venue", true, input("venue", "Journal or conference")),
    field("", "Source type", false, typeSelect),
    field("full-width", "Abstract", true, el("textarea", { attrs: { name: "abstract", maxlength: "20000" } })),
    field("full-width", "Open-access URL", true, input("oa_url", "https://…", { type: "url" }))
  );
  const submit = el("button", { attrs: { type: "submit" }, text: "Add paper" });
  form.append(el("div", { className: "form-actions full-width", children: [submit] }));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    errorSlot.replaceChildren();
    const data = Object.fromEntries(new FormData(form));
    let payload;
    try {
      payload = buildManualOwnPaperPayload(data);
    } catch (error) {
      errorSlot.replaceChildren(ownPaperErrorNode(error.message));
      return;
    }
    submit.disabled = true;
    try {
      await request(projectApi(ctx.project.id, "/sources/own"), { method: "POST", body: JSON.stringify(payload) });
      form.reset();
      toast("Your paper was added.");
      await onSaved();
    } catch (error) {
      errorSlot.replaceChildren(ownPaperErrorNode(error.message));
    } finally {
      submit.disabled = false;
    }
  });
  return form;
}

function ownPaperBibtexForm(ctx, errorSlot, onSaved) {
  const form = el("div");
  const textarea = el("textarea", { attrs: { rows: "6", maxlength: "2000000", placeholder: "@article{key, title={...}, author={...}, year={...}, doi={...}}" } });
  const submit = el("button", { text: "Add from BibTeX" });
  submit.addEventListener("click", async () => {
    errorSlot.replaceChildren();
    let payload;
    try {
      payload = buildBibtexOwnPaperPayload(textarea.value);
    } catch (error) {
      errorSlot.replaceChildren(ownPaperErrorNode(error.message));
      return;
    }
    submit.disabled = true;
    try {
      await request(projectApi(ctx.project.id, "/sources/own"), { method: "POST", body: JSON.stringify(payload) });
      textarea.value = "";
      toast("Your paper was added.");
      await onSaved();
    } catch (error) {
      errorSlot.replaceChildren(ownPaperErrorNode(error.message));
    } finally {
      submit.disabled = false;
    }
  });
  form.append(textarea, el("div", { className: "form-actions", children: [submit] }));
  return form;
}

const OWN_PAPER_MODES = [
  ["lookup", "Look up by DOI or title"],
  ["manual", "Enter details myself"],
  ["bibtex", "Paste BibTeX"],
];

// Form/panel for M2.14: the researcher adds a paper they already have, without going through
// search/screening. Three ways in (lookup, manual entry, BibTeX), one at a time; PDF attachment is
// a separate step on the resulting source card (`attachPdfButton`), since the source must exist first.
function ownPaperSection(ctx) {
  if (!ctx.canWrite) return null;
  const panel = el("div", { className: "panel" });
  panel.append(el("h2", { text: "Add my own paper" }));
  panel.append(
    el("p", {
      className: "helper",
      text: "Add a paper you already have, outside search and screening: look it up by DOI or exact title, enter its details yourself, or paste one BibTeX entry. It is added to your sources unverified; attach its PDF afterwards from the source's card below.",
    })
  );
  const tabs = el("div", { className: "source-actions" });
  const statusSlot = el("div");
  const errorSlot = el("div");
  const bodySlot = el("div");
  const onSaved = async () => {
    resetOwnPaperLookup();
    errorSlot.replaceChildren();
    statusSlot.replaceChildren();
    await ctx.refresh();
  };
  const renderBody = () => {
    errorSlot.replaceChildren();
    Array.from(tabs.children).forEach((button, index) => {
      button.setAttribute("aria-pressed", String(OWN_PAPER_MODES[index][0] === ownPaperMode));
    });
    if (ownPaperMode === "lookup") {
      const status = ownPaperLookupStatusNode();
      statusSlot.replaceChildren(...(status ? [status] : []));
      bodySlot.replaceChildren(ownPaperLookupForm(ctx, errorSlot, statusSlot, onSaved));
    } else if (ownPaperMode === "manual") {
      statusSlot.replaceChildren();
      bodySlot.replaceChildren(ownPaperManualForm(ctx, errorSlot, onSaved));
    } else {
      statusSlot.replaceChildren();
      bodySlot.replaceChildren(ownPaperBibtexForm(ctx, errorSlot, onSaved));
    }
  };
  OWN_PAPER_MODES.forEach(([key, text]) => {
    const button = el("button", { className: "quiet-button", text, attrs: { type: "button", "aria-pressed": String(ownPaperMode === key) } });
    button.addEventListener("click", () => {
      ownPaperMode = key;
      renderBody();
    });
    tabs.append(button);
  });
  panel.append(tabs, statusSlot, bodySlot, errorSlot);
  renderBody();
  return panel;
}

function render(root, ctx) {
  if (currentProjectId !== ctx.project.id) {
    selected = new Set();
    currentProjectId = ctx.project.id;
    ownPaperMode = "lookup";
    resetOwnPaperLookup();
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
  const sortButton = el("button", {
    className: "quiet-button",
    text: "Sort by citations",
    attrs: { "aria-pressed": String(citationSort) },
  });
  sortButton.addEventListener("click", () => {
    citationSort = !citationSort;
    sortButton.setAttribute("aria-pressed", String(citationSort));
    sourcesList(root, ctx);
  });
  toolbar.append(sortButton);
  const minCitationsInput = el("input", {
    attrs: { type: "number", min: "0", placeholder: "Min citations", "aria-label": "Minimum citations" },
  });
  if (minCitations != null) minCitationsInput.value = String(minCitations);
  minCitationsInput.addEventListener("change", () => {
    const value = minCitationsInput.value.trim();
    minCitations = value === "" ? null : Math.max(0, Math.trunc(Number(value)) || 0);
    sourcesList(root, ctx);
  });
  toolbar.append(minCitationsInput);
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
  citationStatsRef = el("p", { className: "muted small", attrs: { id: "source-citation-stats" } });
  panel.append(citationStatsRef);
  panel.append(sourcesList(root, ctx));

  const sections = [];
  sections.push(panel);
  const ownPaper = ownPaperSection(ctx);
  if (ownPaper) sections.push(ownPaper);
  const hint = verificationHint(ctx);
  renderFrame(root, ctx, { stepId: "sources", work: sections, next: [], stepNext: "ask" });
}

function update(root, ctx) {
  if (root.querySelector("#source-list")) sourcesList(root, ctx);
}

export default { render, update };
