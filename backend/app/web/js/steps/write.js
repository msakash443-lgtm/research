import * as PM from "../../vendor/prosemirror/prosemirror.bundle.js";
import { el, emptyCard, formatDate, label, toast } from "../dom.js";
import { request, projectApi } from "../api.js";
import { errorCard } from "./common.js";
import { schema } from "../editor/schema.js";
import { AI_INSERT_META, PROVENANCE_META, newSid, provenancePlugin, resolvePaste, spansOf, summarize } from "../editor/provenance.js";
import { aiLevel } from "../aiLevels.js";
import { suggestionPanel } from "../suggestion.js";
import { clearDraft, loadDraft, saveDraft } from "../editor/draft.js";
import { planConflictResolution, textOf } from "../editor/conflictLogic.js";
import { flagsFor, institutionalCheck, summaryLine } from "../similarityLogic.js";
import { sourceFlagsPlugin, unlinkedRanges } from "../editor/sourceFlags.js";
import { citableSources, citedIdsIn, pickerMatches, sourceLabel } from "../sourcePanelLogic.js";
import { matchingNumbers, numberLabel, runLabel, statAttrs } from "../numberPanelLogic.js";
import { renderReport } from "../provenanceReport.js";
import { manuscriptExportItems } from "../manuscriptExportLogic.js";
import { readOriginColors, writeOriginColors } from "../originColorsLogic.js";

// The writing editor (X.31.4). A section is saved as versions: the editor sends the whole document with the version
// it was built on (`base_number`), and the server adds the next version or refuses (409 when someone else saved first,
// 422 when a rule fails). The editor mirrors the server's provenance rules (editor/provenance.js) so typical saves pass.
// Autosave: 2 s after the last change, at least every 20 s while typing, and when the tab is hidden. Before each
// network save the text is kept in this browser (editor/draft.js). Nothing here is shared between people.
const IDLE_MS = 2000;
const MAX_UNSAVED_MS = 20000;
const TICK_MS = 1000;
const RETRY_MS = 10000;
const ORIGIN_LABELS = {
  typed: "Typed by you", pasted: "Pasted", imported: "Imported", dictated: "Dictated",
  from_note: "From a note", ai_edited: "AI-edited", ai_suggested: "AI suggestion",
};
const PASTE_LABELS = { unconfirmed: "Unconfirmed paste", own: "Own words", quotation: "Quotation" };
const EMPTY_SECTION = { type: "doc", content: [{ type: "paragraph" }] };
// The side panel (X.31.11): the four actions, in the order the panel shows them. Their rules are on the server.
const ASSISTANT_ACTIONS = [
  { value: "challenge", label: "Challenge this", needsFull: false },
  { value: "find_evidence", label: "Find evidence", needsFull: false },
  { value: "argument_check", label: "Check the argument", needsFull: false },
  { value: "clearer_sentence", label: "Clearer wording", needsFull: true },
];
const MAX_SELECTION_CHARS = 2000;
const RESULT_POLL_MS = 3000;
const RESULT_POLL_TIMES = 20;
const SIMILARITY_POLL_TIMES = 20;
const SIMILARITY_POLL_MS = 1000;

let active = null;

export async function render(root, ctx, selectId = null) {
  active?.stop();
  active = null;
  const api = (suffix = "") => projectApi(ctx.project.id, suffix);
  let sections;
  try {
    sections = await request(api("/documents"));
  } catch (error) {
    root.replaceChildren(errorCard(error.message, () => render(root, ctx, selectId)));
    return;
  }
  root.replaceChildren(el("p", { className: "muted small write-head", text: "Each save adds a version. Nothing is deleted, and any earlier version can be restored." }));

  const sectionsBar = el("div", { className: "write-sections" });
  root.append(sectionsBar);
  if (sections.length === 0) {
    root.append(emptyCard("No sections yet", ctx.canWrite
      ? "Add a section to start writing."
      : "The owner or a co-author adds sections. You can read them here once they exist."));
  }
  if (ctx.canWrite) sectionsBar.append(addSectionForm(api, ctx, root, sections));
  if (sections.length === 0) return;

  const chosen = sections.find((section) => section.id === selectId) || sections[0];
  const picker = el("select", { attrs: { "aria-label": "Section" } });
  for (const section of sections) picker.append(el("option", { text: section.title, attrs: { value: section.id } }));
  picker.value = chosen.id;
  picker.addEventListener("change", () => render(root, ctx, picker.value));
  sectionsBar.prepend(el("label", { className: "write-picker", children: [el("span", { text: "Section" }), picker] }));

  active = await openSection(root, ctx, chosen);
}

function addSectionForm(api, ctx, root, sections) {
  const title = el("input", { attrs: { "aria-label": "New section title", maxlength: "300", placeholder: "New section title" } });
  const button = el("button", { type: "button", className: "quiet-button", text: "Add section" });
  const nextPosition = sections.reduce((highest, section) => Math.max(highest, section.position), 0) + 1;
  button.addEventListener("click", async () => {
    const name = title.value.trim();
    if (!name) {
      toast("Give the section a title first.");
      return;
    }
    try {
      const created = await request(api("/documents"), { method: "POST", body: JSON.stringify({ title: name, position: nextPosition }) });
      toast(`Section "${created.title}" added.`);
      await render(root, ctx, created.id);
    } catch (error) {
      toast(error.message);
    }
  });
  return el("form", { className: "write-add", children: [title, button], attrs: { "aria-label": "Add a section" } });
}

// Opens one section in the editor and wires autosave. Returns the session so the next render can stop it.
async function openSection(root, ctx, section) {
  const api = (suffix = "") => projectApi(ctx.project.id, suffix);
  const me = String(ctx.user?.id || "");
  const documentPath = api(`/documents/${section.id}`);

  let latest = null;
  try {
    latest = await request(documentPath);
  } catch (error) {
    if (error.status !== 404) {
      root.append(errorCard(error.message, () => render(root, ctx, section.id)));
      return { stop() {} };
    }
  }
  const base = latest ? latest.number : 0;
  let doc;
  try {
    doc = PM.model.Node.fromJSON(schema, latest ? latest.content_json : EMPTY_SECTION);
  } catch (error) {
    root.append(errorCard(`This section can't be opened: ${error.message}`, () => render(root, ctx, section.id)));
    return { stop() {} };
  }

  const status = el("p", { className: "write-status", attrs: { role: "status", "aria-live": "polite" } });
  const banner = el("div", { className: "write-banner", attrs: { role: "status" } });
  banner.hidden = true;
  // Version-conflict compare view (X.31.16): shown when a save's base_number is behind the server's latest.
  const conflictBanner = el("div", { className: "write-banner write-conflict-banner", attrs: { role: "status" } });
  conflictBanner.hidden = true;
  const editorEl = el("div", { className: "write-editor" });
  const panelBody = el("div", { className: "write-panel-body" });
  const tabs = {
    record: el("button", { type: "button", className: "quiet-button write-tab", text: "Record", attrs: { role: "tab", "aria-selected": "true" } }),
    versions: el("button", { type: "button", className: "quiet-button write-tab", text: "Versions", attrs: { role: "tab", "aria-selected": "false" } }),
    assistant: el("button", { type: "button", className: "quiet-button write-tab", text: "Assistant", attrs: { role: "tab", "aria-selected": "false" } }),
    similarity: el("button", { type: "button", className: "quiet-button write-tab", text: "Similarity", attrs: { role: "tab", "aria-selected": "false" } }),
    sources: el("button", { type: "button", className: "quiet-button write-tab", text: "Sources", attrs: { role: "tab", "aria-selected": "false" } }),
    numbers: el("button", { type: "button", className: "quiet-button write-tab", text: "Numbers", attrs: { role: "tab", "aria-selected": "false" } }),
    report: el("button", { type: "button", className: "quiet-button write-tab", text: "Report", attrs: { role: "tab", "aria-selected": "false" } }),
  };
  const focusButton = el("button", { type: "button", className: "quiet-button", text: "Focus mode", attrs: { "aria-pressed": "false" } });
  const exportMenu = el("details", { className: "write-export-menu", children: [
    el("summary", { className: "quiet-button", text: "Export ▾" }),
    el("div", { className: "write-export-list", children: manuscriptExportItems(ctx.project.id).map((item) =>
      el("a", { text: item.label, href: item.href, attrs: { download: "" } })) }),
  ] });
  exportMenu.addEventListener("click", (event) => { if (event.target.closest("a")) exportMenu.open = false; });
  const toolbar = el("div", { className: "write-toolbar", attrs: { role: "toolbar", "aria-label": "Formatting" } });
  const layout = el("div", { className: "write-layout", children: [
    el("div", { className: "write-main", children: [toolbar, status, banner, conflictBanner, editorEl] }),
    el("aside", { className: "write-panel", attrs: { "aria-label": "Section record, versions and AI assistant" }, children: [
      el("div", { className: "write-tabs", attrs: { role: "tablist" }, children: [tabs.record, tabs.versions, tabs.assistant, tabs.similarity, tabs.sources, tabs.numbers, tabs.report] }),
      panelBody,
    ] }),
  ] });
  root.append(layout);

  const s = {
    base, dirty: false, firstDirtyAt: 0, lastChangeAt: 0, inFlight: false, paused: false,
    refusedSnapshot: null, retryAfter: 0, tab: "record", historyFor: null, sessionId: newSid(), view: null, stop: null,
    resultsEl: null, pollTimer: null, similarityTimer: null,
    // The last saved version's id and plain text: a similarity flag's offsets point into this text (X.31.14).
    latestId: latest?.id ?? null, latestText: latest?.content_text ?? "",
    originColors: readOriginColors(window.localStorage, ctx.project.id),
  };
  editorEl.classList.toggle("origin-colors-off", !s.originColors);

  const setStatus = (text, isError = false) => {
    status.textContent = text;
    status.classList.toggle("write-status-error", isError);
  };

  const markDirty = () => {
    const now = Date.now();
    s.dirty = true;
    s.lastChangeAt = now;
    if (!s.firstDirtyAt) s.firstDirtyAt = now;
    if (!s.paused) setStatus("Unsaved changes");
  };

  const save = async ({ keepalive = false } = {}) => {
    if (!s.dirty || s.inFlight || s.paused || !s.view) return;
    const content = s.view.state.doc.toJSON();
    const snapshot = JSON.stringify(content);
    if (snapshot === s.refusedSnapshot) return;
    const clientSaveId = newSid();
    s.inFlight = true;
    setStatus("Saving…");
    const kept = await saveDraft({
      document_id: section.id, base_number: s.base, client_save_id: clientSaveId, content_json: content,
      saved_at: new Date().toISOString(),
    });
    if (!kept) toast("This browser could not keep a local copy of your text. The server save still goes ahead.");
    try {
      const result = await request(`${documentPath}/versions`, {
        method: "POST",
        keepalive,
        body: JSON.stringify({
          base_number: s.base, client_save_id: clientSaveId, client_session_id: s.sessionId,
          client_saved_at: new Date().toISOString(), content_json: content,
        }),
      });
      s.base = result.version.number;
      s.latestId = result.version.id;
      s.latestText = result.version.content_text;
      s.retryAfter = 0;
      if (!s.view) return; // the person left the step while this save was in flight
      if (JSON.stringify(s.view.state.doc.toJSON()) === snapshot) {
        s.dirty = false;
        s.firstDirtyAt = 0;
        await clearDraft(section.id);
      } else {
        s.firstDirtyAt = Date.now();
      }
      setStatus(s.dirty ? "Unsaved changes" : `Saved · version ${s.base}`);
      if (result.detected?.length) {
        toast(`${result.detected.length} pasted passage(s) match this project's sources. Check them in Record.`);
      }
      renderPanel();
    } catch (error) {
      if (error.status === 409) {
        s.paused = true;
        const theirsVersion = error.detail?.current;
        if (theirsVersion) {
          showConflict(content, theirsVersion);
        } else {
          setStatus("Saved elsewhere: this section has a newer version. Reload the section to continue; your text stays in this browser.", true);
        }
      } else if (error.status === 422) {
        s.refusedSnapshot = snapshot;
        setStatus(`Not saved: ${error.message}. Fix it and keep writing, or see Record.`, true);
      } else {
        s.retryAfter = Date.now() + RETRY_MS;
        setStatus("Not saved: no connection. Your text is kept here and it will be sent again.", true);
      }
    } finally {
      s.inFlight = false;
    }
  };

  const replaceText = (newDoc, { dirty }) => {
    const tr = s.view.state.tr.replaceWith(0, s.view.state.doc.content.size, newDoc.content).setMeta(PROVENANCE_META, "load");
    s.view.dispatch(tr);
    if (dirty) markDirty();
  };

  // Version-conflict compare view (X.31.16): the server's 409 `version_conflict` carries the current saved version
  // (`theirsVersion`). `mineJson` is the content this browser tried and failed to save. Shows both in full, plain
  // text, and lets the researcher choose instead of silently losing either side.
  const showConflict = (mineJson, theirsVersion) => {
    const mineText = textOf(mineJson);
    const theirsText = textOf(theirsVersion.content_json);
    const keepMine = el("button", { type: "button", text: "Keep mine" });
    const keepTheirs = el("button", { type: "button", className: "quiet-button", text: "Keep theirs" });
    const keepBoth = el("button", { type: "button", className: "quiet-button", text: "Keep both" });
    conflictBanner.replaceChildren(
      el("p", { text: `This section has a newer version (version ${theirsVersion.number}), saved elsewhere while you were writing. Compare the two and choose what to keep.` }),
      el("div", { className: "write-conflict-compare", children: [
        el("div", { className: "write-conflict-col", children: [el("h4", { text: "Your unsaved text" }), el("pre", { text: mineText || "(empty)" })] }),
        el("div", { className: "write-conflict-col", children: [el("h4", { text: `Saved on the server · version ${theirsVersion.number}` }), el("pre", { text: theirsText || "(empty)" })] }),
      ] }),
      el("div", { className: "write-conflict-actions", children: [keepMine, keepTheirs, keepBoth] }),
    );
    conflictBanner.hidden = false;
    setStatus("Saved elsewhere: this section has a newer version. Compare below and choose what to keep; your text stays in this browser until you decide.", true);
    keepMine.addEventListener("click", () => resolveVersionConflict(mineJson, theirsVersion, "mine"));
    keepTheirs.addEventListener("click", () => resolveVersionConflict(mineJson, theirsVersion, "theirs"));
    keepBoth.addEventListener("click", () => resolveVersionConflict(mineJson, theirsVersion, "both"));
  };

  // Applies the researcher's keep-mine/keep-theirs/keep-both choice (`editor/conflictLogic.js`, reusing
  // `offlineOutbox.js`'s generic `resolveConflict` from X.31.5 — the first real caller it gets). "theirs" shows the
  // server's text and stops; "mine"/"both" put the resulting text in the editor and immediately retry the save
  // against the server's new base_number.
  const resolveVersionConflict = async (mineJson, theirsVersion, choice) => {
    const plan = planConflictResolution(mineJson, theirsVersion.content_json, choice);
    s.base = theirsVersion.number;
    conflictBanner.hidden = true;
    conflictBanner.replaceChildren();
    if (plan.action === "drop") {
      const theirsDoc = safeDoc(plan.content_json);
      if (theirsDoc) replaceText(theirsDoc, { dirty: false });
      s.latestId = theirsVersion.id;
      s.latestText = theirsVersion.content_text;
      s.paused = false;
      s.dirty = false;
      s.firstDirtyAt = 0;
      await clearDraft(section.id);
      setStatus(`Kept the saved version · version ${s.base}`);
      renderPanel();
      return;
    }
    if (choice === "both") {
      const mergedDoc = safeDoc(plan.content_json);
      if (mergedDoc) replaceText(mergedDoc, { dirty: true });
    } else {
      markDirty();
    }
    s.paused = false;
    await save();
  };

  const tick = () => {
    if (!editorEl.isConnected) return s.stop();
    if (!s.dirty || s.inFlight || s.paused) return;
    const now = Date.now();
    if (now < s.retryAfter) return;
    if (now - s.lastChangeAt >= IDLE_MS || now - s.firstDirtyAt >= MAX_UNSAVED_MS) save();
  };
  const onHidden = () => {
    if (document.visibilityState === "hidden") save({ keepalive: true });
  };
  const timer = window.setInterval(tick, TICK_MS);
  document.addEventListener("visibilitychange", onHidden);
  window.addEventListener("pagehide", onHidden);

  s.stop = () => {
    window.clearInterval(timer);
    window.clearInterval(s.pollTimer);
    document.removeEventListener("visibilitychange", onHidden);
    window.removeEventListener("pagehide", onHidden);
    if (s.dirty && !s.paused) save({ keepalive: true });
    s.view?.destroy();
    s.view = null;
  };

  const keys = {
    "Mod-z": PM.history.undo,
    "Mod-y": PM.history.redo,
    "Mod-Shift-z": PM.history.redo,
    "Mod-b": PM.commands.toggleMark(schema.marks.strong),
    "Mod-i": PM.commands.toggleMark(schema.marks.em),
  };
  const state = PM.state.EditorState.create({
    schema,
    doc,
    plugins: [
      PM.history.history(),
      PM.keymap.keymap(keys),
      PM.keymap.keymap(PM.commands.baseKeymap),
      provenancePlugin({ actor: me }),
      sourceFlagsPlugin({ onAt: openSourcesForAt }),
      PM.dropcursor.dropCursor(),
      PM.gapcursor.gapCursor(),
    ],
  });
  s.view = new PM.view.EditorView(editorEl, {
    state,
    editable: () => ctx.canWrite,
    attributes: { role: "textbox", "aria-multiline": "true", "aria-label": `Text of ${section.title}`, class: "write-prose" },
    dispatchTransaction(tr) {
      const next = s.view.state.apply(tr);
      s.view.updateState(next);
      const meta = tr.getMeta(PROVENANCE_META);
      if (tr.docChanged && meta !== "load") markDirty();
      if (meta === "resolve") {
        renderPanel();
        save();
      }
      if (tr.selectionSet && (s.tab === "assistant" || s.tab === "sources")) renderPanel();
    },
  });

  // Drafts: a local copy is offered back, never loaded without the person asking.
  const draft = await loadDraft(section.id);
  if (draft) {
    const draftDoc = safeDoc(draft.content_json);
    const same = draftDoc && JSON.stringify(draftDoc.toJSON()) === JSON.stringify(doc.toJSON());
    if (!draftDoc || same) {
      await clearDraft(section.id);
    } else {
      const older = draft.base_number !== base;
      const useIt = el("button", { type: "button", text: older ? "Use my unsaved text anyway" : "Use my unsaved text" });
      const discard = el("button", { type: "button", className: "quiet-button", text: "Discard it" });
      const when = formatDate(draft.saved_at);
      const kept = `This browser kept unsaved text for this section${when ? ` (${when})` : ""}.`;
      const warning = older
        ? " It is from before the latest saved version. Using it replaces the current text here, but the current version stays in Versions."
        : "";
      banner.replaceChildren(
        el("span", { text: kept + warning }),
        useIt,
        discard,
      );
      banner.hidden = false;
      useIt.addEventListener("click", () => {
        replaceText(draftDoc, { dirty: true });
        banner.hidden = true;
      });
      discard.addEventListener("click", async () => {
        await clearDraft(section.id);
        banner.hidden = true;
      });
    }
  }

  const applyFocus = (on) => {
    layout.classList.toggle("write-focus", on);
    focusButton.setAttribute("aria-pressed", String(on));
    s.view?.focus();
  };
  focusButton.addEventListener("click", () => applyFocus(!layout.classList.contains("write-focus")));
  const bold = el("button", { type: "button", className: "quiet-button", text: "Bold", attrs: { "aria-keyshortcuts": "Control+B" } });
  const italic = el("button", { type: "button", className: "quiet-button", text: "Italic", attrs: { "aria-keyshortcuts": "Control+I" } });
  const undo = el("button", { type: "button", className: "quiet-button", text: "Undo", attrs: { "aria-keyshortcuts": "Control+Z" } });
  const redo = el("button", { type: "button", className: "quiet-button", text: "Redo", attrs: { "aria-keyshortcuts": "Control+Y" } });
  bold.addEventListener("click", () => keys["Mod-b"](s.view.state, s.view.dispatch, s.view));
  italic.addEventListener("click", () => keys["Mod-i"](s.view.state, s.view.dispatch, s.view));
  undo.addEventListener("click", () => PM.history.undo(s.view.state, s.view.dispatch));
  redo.addEventListener("click", () => PM.history.redo(s.view.state, s.view.dispatch));
  toolbar.replaceChildren(bold, italic, undo, redo, focusButton, exportMenu);
  if (!ctx.canWrite) toolbar.replaceChildren(el("p", { className: "muted small", text: "Read only for your role." }));

  const setTab = (name) => {
    s.tab = name;
    for (const [key, node] of Object.entries(tabs)) node.setAttribute("aria-selected", String(key === name));
    renderPanel();
  };
  tabs.record.addEventListener("click", () => setTab("record"));
  tabs.versions.addEventListener("click", () => setTab("versions"));
  tabs.assistant.addEventListener("click", () => setTab("assistant"));
  tabs.similarity.addEventListener("click", () => setTab("similarity"));
  tabs.sources.addEventListener("click", () => setTab("sources"));
  tabs.numbers.addEventListener("click", () => setTab("numbers"));
  tabs.report.addEventListener("click", () => setTab("report"));
  layout.addEventListener("keydown", (event) => {
    if (event.altKey && event.key.toLowerCase() === "s") {
      event.preventDefault();
      setTab(s.tab === "record" ? "versions" : "record");
      tabs[s.tab].focus();
    }
    if (event.key === "Escape" && layout.classList.contains("write-focus")) applyFocus(false);
  });

  const renderPanel = () => {
    if (!s.view) return; // the step was left: nothing to show
    if (s.tab === "record") return renderRecord();
    if (s.tab === "versions") return renderVersions();
    if (s.tab === "similarity") return renderSimilarity();
    if (s.tab === "sources") return renderSources();
    if (s.tab === "numbers") return renderNumbers();
    if (s.tab === "report") return renderReportTab();
    return renderAssistant();
  };

  // The Sources tab (X.31.10). Verified sources to cite (also offered by "@"), the sources the selection relies on,
  // the passages saved for each source, and the sentences that state a number with no source. Passages are read and
  // written through the server; the server decides who may read or add them.
  function openSourcesForAt() {
    if (!s.view) return;
    s.atPicker = true;
    setTab("sources");
  }

  async function renderSources() {
    panelBody.replaceChildren(el("p", { className: "muted small", text: "Loading sources…" }));
    let sources;
    try {
      sources = await request(api("/sources"));
    } catch (error) {
      panelBody.replaceChildren(el("p", { className: "small", text: error.message }));
      return;
    }
    if (!s.view || s.tab !== "sources") return;

    const byId = new Map(sources.map((source) => [source.id, source]));
    const { from, to } = s.view.state.selection;
    const relied = [];
    s.view.state.doc.nodesBetween(from, to, (node) => {
      if (node.type.name === "cite") relied.push(node.attrs.source_id);
    });
    const reliedSources = citedIdsIn(relied).map((id) => byId.get(id)).filter(Boolean);
    const reliedBlock = el("div", { className: "write-relied", children: [
      el("h4", { text: "Sources this selection relies on" }),
      reliedSources.length
        ? el("ul", { children: reliedSources.map((source) => el("li", { children: [
          el("p", { className: "small", text: `${sourceLabel(source)} · ${source.title}` }),
          passagesToggle(source),
        ] })) })
        : el("p", { className: "muted small", text: "Select a sentence with a citation to see its sources." }),
    ] });

    const flagged = unlinkedRanges(s.view.state.doc);
    const flagBlock = el("div", { className: "write-flags-block", children: [
      el("h4", { text: "No source linked yet" }),
      flagged.length
        ? el("ul", { className: "write-flags", children: flagged.map((flag) => el("li", { className: "small", text: `“${excerpt(flag.text, 160)}”` })) })
        : el("p", { className: "muted small", text: "Every sentence with a number has a source." }),
    ] });

    const search = el("input", { attrs: { type: "search", "aria-label": "Search verified sources", placeholder: "Search verified sources" } });
    const list = el("ul", { className: "write-source-list" });
    const fill = () => {
      const matches = search.value.trim() ? citableSources(sources, search.value) : pickerMatches(sources, "");
      list.replaceChildren(...(matches.length
        ? matches.map((source) => el("li", { children: [
          el("p", { className: "small", text: `${sourceLabel(source)} · ${source.title}` }),
          citeButton(source),
          passagesToggle(source),
        ] }))
        : [el("li", { className: "muted small", text: sources.length ? "No verified source matches." : "No sources yet." })]));
    };
    search.addEventListener("input", fill);
    fill();

    panelBody.replaceChildren(
      el("h3", { text: "Sources" }),
      el("p", { className: "small", text: "Only verified sources can be cited. Typing “@” in the text opens this list." }),
      reliedBlock,
      flagBlock,
      el("h4", { text: "Verified sources" }),
      search,
      list,
    );
    if (s.atPicker) search.focus();
  }

  function citeButton(source) {
    const button = el("button", { type: "button", className: "quiet-button", text: "Cite here" });
    if (!ctx.canWrite) button.disabled = true;
    button.addEventListener("click", () => insertCite(source));
    return button;
  }

  function insertCite(source) {
    if (!ctx.canWrite || !s.view) return;
    const { state } = s.view;
    const { empty, from } = state.selection;
    let tr = state.tr;
    // "@" opened the picker: remove it so the citation takes its place.
    if (s.atPicker && empty && from > 1 && state.doc.textBetween(from - 1, from) === "@") {
      tr = tr.delete(from - 1, from);
    }
    s.atPicker = false;
    tr = tr.replaceSelectionWith(schema.nodes.cite.create({ source_id: source.id }), false).scrollIntoView();
    s.view.dispatch(tr);
    s.view.focus();
  }

  // The Numbers tab (M6.3.1). Lists this project's AnalysisRun outputs, flattened to dot-paths (app/number_citation.py
  // ::flatten_numbers, via GET .../documents/numbers), so a number is always chosen from a real computed value, never
  // typed. "Insert" places a `stat` node at the cursor, the only way a number enters a section (app/ledger.py enforces
  // this at save time: an unresolvable path is refused as `unresolved_number`).
  async function renderNumbers() {
    panelBody.replaceChildren(el("p", { className: "muted small", text: "Loading numbers…" }));
    let options;
    try {
      options = await request(api("/numbers"));
    } catch (error) {
      panelBody.replaceChildren(el("p", { className: "small", text: error.message }));
      return;
    }
    if (!s.view || s.tab !== "numbers") return;

    const multipleRuns = new Set(options.map((o) => o.analysis_run_id)).size > 1;
    const search = el("input", { attrs: { type: "search", "aria-label": "Search analysis numbers", placeholder: "Search numbers (e.g. age.mean)" } });
    const list = el("ul", { className: "write-number-list" });
    const fill = () => {
      const matches = matchingNumbers(options, search.value);
      list.replaceChildren(...(matches.length
        ? matches.map((option) => el("li", { children: [
          el("p", { className: "small", text: multipleRuns ? `${numberLabel(option)} (run ${runLabel(option.analysis_run_id)})` : numberLabel(option) }),
          numberButton(option),
        ] }))
        : [el("li", { className: "muted small", text: options.length ? "No number matches." : "No analysis run outputs in this project yet." })]));
    };
    search.addEventListener("input", fill);
    fill();

    panelBody.replaceChildren(
      el("h3", { text: "Numbers" }),
      el("p", { className: "small", text: "Only numbers from this project's analysis runs can be inserted; typed digits are never verified." }),
      search,
      list,
    );
  }

  function numberButton(option) {
    const button = el("button", { type: "button", className: "quiet-button", text: "Insert" });
    if (!ctx.canWrite) button.disabled = true;
    button.addEventListener("click", () => insertStat(option));
    return button;
  }

  function insertStat(option) {
    if (!ctx.canWrite || !s.view) return;
    const tr = s.view.state.tr.replaceSelectionWith(schema.nodes.stat.create(statAttrs(option)), false).scrollIntoView();
    s.view.dispatch(tr);
    s.view.focus();
  }

  // A source's saved passages, loaded on demand. Adding one is offered to writers; the server decides what is allowed.
  function passagesToggle(source) {
    const holder = el("div", { className: "write-passages" });
    const toggle = el("button", { type: "button", className: "quiet-button", text: "Passages", attrs: { "aria-expanded": "false" } });
    toggle.addEventListener("click", () => {
      const open = toggle.getAttribute("aria-expanded") !== "true";
      toggle.setAttribute("aria-expanded", String(open));
      if (open) loadPassages(holder, source);
      else holder.replaceChildren();
    });
    return el("div", { children: [toggle, holder] });
  }

  async function loadPassages(holder, source) {
    holder.replaceChildren(el("p", { className: "muted small", text: "Loading passages…" }));
    let rows;
    try {
      rows = await request(api(`/sources/${source.id}/excerpts`));
    } catch (error) {
      holder.replaceChildren(el("p", { className: "small", text: error.message }));
      return;
    }
    const items = rows.map((row) => el("li", { children: [
      el("p", { className: "small", text: `“${excerpt(row.content, 200)}”` }),
      el("p", { className: "muted small", text: row.locator || "No page or section given" }),
    ] }));
    const children = [items.length ? el("ul", { children: items }) : el("p", { className: "muted small", text: "No passages saved yet." })];
    if (ctx.canWrite) children.push(passageForm(holder, source));
    holder.replaceChildren(...children);
  }

  function passageForm(holder, source) {
    const content = el("textarea", { attrs: { "aria-label": "Passage from this source", rows: "3", maxlength: "5000" } });
    const locator = el("input", { attrs: { type: "text", "aria-label": "Page or section", maxlength: "255", placeholder: "Page or section (optional)" } });
    const save = el("button", { type: "button", text: "Save passage" });
    save.addEventListener("click", async () => {
      const text = content.value.trim();
      if (!text) {
        toast("Type or paste the passage first.");
        return;
      }
      save.disabled = true;
      try {
        await request(api(`/sources/${source.id}/excerpts`), {
          method: "POST",
          body: JSON.stringify({ content: text, locator: locator.value.trim() || null }),
        });
        toast("Passage saved.");
        await loadPassages(holder, source);
      } catch (error) {
        toast(error.message);
        save.disabled = false;
      }
    });
    return el("div", { className: "write-passage-form", children: [content, locator, save] });
  }

  // The similarity panel (X.31.14). It checks the last saved version against this project's sources on the server,
  // and shows each flag with the sentence it points at. Flags are never shown for an older version.
  // The Report tab (X.31.12.2): this project's writing record. Reads any role; the server decides what each role sees.
  function renderReportTab() {
    return renderReport(panelBody, { projectId: ctx.project.id, section: section ? { id: section.id, title: section.title } : null });
  }

  async function renderSimilarity() {
    panelBody.replaceChildren(el("p", { className: "muted small", text: "Loading checks…" }));
    let checks;
    try {
      checks = await request(`${api("/similarity-checks")}?document_id=${encodeURIComponent(section.id)}`);
    } catch (error) {
      panelBody.replaceChildren(el("p", { className: "small", text: error.message }));
      return;
    }
    const providers = await request(api("/similarity-providers")).catch(() => null);
    const last = checks[0] || null;
    const shown = flagsFor(last, s.latestId, s.latestText);
    const reason = !ctx.canWrite ? "Read only for your role." : !s.latestId ? "Save the section first." : "";
    const check = el("button", { type: "button", text: last ? "Check again" : "Check similarity", attrs: reason ? { disabled: "disabled", title: reason } : {} });
    check.addEventListener("click", runCheck);

    const outcome = !last
      ? el("p", { className: "muted small", text: "Not checked yet." })
      : shown.state === "older"
        ? el("p", { className: "small", text: "This check ran on an older version. Check again to see the current text." })
        : el("p", { className: "small", text: `${summaryLine(last.summary).replace(/\.$/, "")}. Checked ${formatDate(last.created_at)}.` });
    const flags = shown.flags.map((flag) => el("li", { className: "write-flag", children: [
      el("p", { className: "small", text: `“${excerpt(flag.quote, 200)}”` }),
      el("p", { className: "muted small", text: flag.hint }),
    ] }));

    panelBody.replaceChildren(
      el("h3", { text: "Similarity" }),
      el("p", { className: "small", text: "Compares the last saved version of this section with this project's sources (their excerpts and abstracts). Nothing leaves this server." }),
      el("p", { className: "muted small", text: "Flags are prompts, not verdicts. Unsaved changes are not checked." }),
      check,
      institutionalButton(institutionalCheck(providers)),
      outcome,
      flags.length ? el("ul", { className: "write-flags", children: flags }) : el("span"),
    );
  }

  // The institutional check (X.31.15) has no connector yet, so the button stays disabled and says why.
  function institutionalButton(state) {
    return el("div", { className: "write-institutional", children: [
      el("button", { type: "button", className: "quiet-button", text: state.label, attrs: state.disabled ? { disabled: "disabled", title: state.hint } : {} }),
      state.hint ? el("p", { className: "muted small", text: state.hint }) : el("span"),
    ] });
  }

  // The check is queued (X.31.14), so the newest check is fetched again until one newer than `previousId` appears.
  async function runCheck() {
    let previousId = null;
    try {
      const before = await request(`${api("/similarity-checks")}?document_id=${encodeURIComponent(section.id)}`);
      previousId = before[0] ? before[0].id : null;
      await request(api("/similarity-checks"), { method: "POST", body: JSON.stringify({ document_id: section.id }) });
    } catch (error) {
      toast(error.message);
      return;
    }
    pollSimilarity(previousId);
  }

  function pollSimilarity(previousId) {
    window.clearInterval(s.similarityTimer);
    let tries = 0;
    s.similarityTimer = window.setInterval(async () => {
      tries += 1;
      let checks = [];
      try {
        checks = await request(`${api("/similarity-checks")}?document_id=${encodeURIComponent(section.id)}`);
      } catch {
        checks = [];
      }
      const newest = checks[0] ? checks[0].id : null;
      if (newest !== previousId || tries >= SIMILARITY_POLL_TIMES) {
        window.clearInterval(s.similarityTimer);
        renderSimilarity();
      }
    }, SIMILARITY_POLL_MS);
  }

  function renderRecord() {
    const doc = s.view.state.doc;
    const summary = summarize(doc);
    const spans = [...spansOf(doc).values()];
    const byOrigin = Object.entries(summary.byOrigin).map(([origin, count]) =>
      el("li", { text: `${ORIGIN_LABELS[origin] || label(origin)}: ${count} characters` }));
    const unconfirmed = spans.filter((span) => span.origin === "pasted" && span.paste === "unconfirmed");
    const resolver = unconfirmed.map((span) => {
      const own = el("button", { type: "button", className: "quiet-button", text: "Own words" });
      const quote = el("button", { type: "button", className: "quiet-button", text: "Quotation" });
      own.addEventListener("click", () => resolve(span.sid, "own"));
      quote.addEventListener("click", () => resolve(span.sid, "quotation"));
      return el("li", { children: [
        el("p", { className: "small", text: `“${excerpt(span.text)}”` }),
        el("p", { className: "muted small", text: "Mark it as your own words, or as a quotation. A quotation needs a citation in the same paragraph." }),
        own, quote,
      ] });
    });
    const spanRows = spans.map((span) => el("li", { children: [
      el("span", { className: "small", text: `${ORIGIN_LABELS[span.origin] || label(span.origin)}${span.paste ? ` · ${PASTE_LABELS[span.paste] || span.paste}` : ""} · ${span.text.length} characters · “${excerpt(span.text, 60)}”` }),
      historyButton(span.sid),
    ] }));
    panelBody.replaceChildren(
      el("h3", { text: "This section's record" }),
      el("p", { className: "small", text: `${summary.characters} characters. Only what you typed counts as typed. Pasted text stays marked until you say what it is.` }),
      (() => {
        const labelNode = el("label", { className: "write-origin-toggle" });
        const input = el("input", { type: "checkbox", attrs: { "aria-label": "Show origin colours" } });
        input.checked = s.originColors;
        input.addEventListener("change", () => {
          s.originColors = input.checked;
          editorEl.classList.toggle("origin-colors-off", !s.originColors);
          writeOriginColors(window.localStorage, ctx.project.id, s.originColors);
        });
        labelNode.append(input, document.createTextNode(" Show origin colours"));
        return labelNode;
      })(),
      el("ul", { children: byOrigin }),
      el("h4", { text: "Unconfirmed pastes" }),
      resolver.length ? el("ul", { className: "write-resolver", children: resolver }) : el("p", { className: "muted small", text: "None." }),
      el("h4", { text: "Every span" }),
      spanRows.length ? el("ul", { children: spanRows }) : el("p", { className: "muted small", text: "Nothing written yet." }),
      s.historyFor ? el("div", { attrs: { id: "write-history" } }) : el("span"),
    );
    if (s.historyFor) loadHistory(s.historyFor);
  }

  // The AI side panel (X.31.11.2). It sends only the selected text, after a preview and an explicit Send. Results are the
  // shared suggestion cards: accept, edit, discard, and (for accepted clearer wording) Insert.
  function renderAssistant() {
    const { from, to } = s.view.state.selection;
    const selected = s.view.state.doc.textBetween(from, to, "\n").trim();
    const involvement = ctx.project.ai_involvement;
    const level = aiLevel(involvement);
    const reasonFor = (action) => {
      if (!ctx.canWrite) return "Read only for your role.";
      if (involvement === "off") return "AI is off for this project.";
      if (action.needsFull && involvement !== "full") return "Needs the Full AI level.";
      if (!selected) return "Select the sentence or passage first.";
      if (selected.length > MAX_SELECTION_CHARS) return `Select up to ${MAX_SELECTION_CHARS} characters.`;
      return "";
    };
    const previewEl = el("div", { className: "write-assistant-preview", attrs: { role: "status", "aria-live": "polite" } });
    const buttons = ASSISTANT_ACTIONS.map((action) => {
      const reason = reasonFor(action);
      const node = el("button", { type: "button", className: "quiet-button", text: action.label, attrs: reason ? { disabled: "disabled", title: reason } : {} });
      node.addEventListener("click", () => previewAction(action, selected));
      return node;
    });
    const reasons = [...new Set(ASSISTANT_ACTIONS.map(reasonFor).filter(Boolean))];
    const draftReason = () => {
      if (!ctx.canWrite) return "Read only for your role.";
      if (involvement === "off") return "AI is off for this project.";
      return "";
    };
    const draftButton = el("button", { type: "button", className: "quiet-button", text: "Draft this section from its claims", attrs: draftReason() ? { disabled: "disabled", title: draftReason() } : {} });
    draftButton.addEventListener("click", requestDraft);
    const resultsEl = el("div", { className: "write-assistant-results" });
    s.resultsEl = resultsEl;
    panelBody.replaceChildren(
      el("h3", { text: "AI assistant" }),
      el("p", { className: "small", text: `${level.label}. ${level.help}` }),
      el("p", { className: "muted small", text: selected ? `Selected: “${excerpt(selected, 120)}”` : "Select a sentence or passage in the section to use the assistant." }),
      el("div", { className: "write-assistant-actions", children: buttons }),
      reasons.length ? el("p", { className: "muted small", text: reasons.join(" ") }) : el("span"),
      el("h4", { text: "Draft this section" }),
      el("p", { className: "muted small", text: "Drafts a starting paragraph from this section's claim outline (X.46.8). It is a rough starting point, not finished wording: it appears below as a suggestion, and nothing is added to your text unless you accept it and press Insert." }),
      el("div", { className: "write-assistant-actions", children: [draftButton] }),
      previewEl,
      el("h4", { text: "Proposals" }),
      el("p", { className: "muted small", text: "Proposals are not yours until you accept them. Nothing from them is added to your text unless you press Insert." }),
      resultsEl,
    );
    mountResults();
  }

  async function previewAction(action, text) {
    const previewEl = panelBody.querySelector(".write-assistant-preview");
    if (!previewEl) return;
    previewEl.replaceChildren(el("p", { className: "muted small", text: "Checking what would be sent…" }));
    try {
      const preview = await request(api("/ai/suggestions/preview"), {
        method: "POST",
        body: JSON.stringify({ action: action.value, text, document_id: section.id }),
      });
      const send = el("button", { type: "button", text: `Send to ${preview.model_route === "local" ? "the local model" : "AI"}` });
      const cancel = el("button", { type: "button", className: "quiet-button", text: "Cancel" });
      cancel.addEventListener("click", () => previewEl.replaceChildren());
      send.addEventListener("click", () => sendAction(action, text));
      previewEl.replaceChildren(
        el("p", { className: "small", text: `${action.label}: ${preview.words} words (${preview.characters} characters) would be sent to ${preview.sends_to}.` }),
        preview.sources_sent
          ? el("p", { className: "small", text: `Plus ${preview.sources_sent} source excerpts from this project, marked as untrusted data.` })
          : el("span"),
        el("p", { className: "muted small", text: preview.note }),
        el("div", { className: "write-assistant-confirm", children: [send, cancel] }),
      );
    } catch (error) {
      previewEl.replaceChildren(el("p", { className: "small", text: error.message }));
    }
  }

  async function sendAction(action, text) {
    const previewEl = panelBody.querySelector(".write-assistant-preview");
    try {
      await request(api("/ai/suggestions"), {
        method: "POST",
        body: JSON.stringify({ action: action.value, text, document_id: section.id }),
      });
    } catch (error) {
      if (previewEl) previewEl.replaceChildren(el("p", { className: "small", text: error.message }));
      return;
    }
    if (previewEl) previewEl.replaceChildren(el("p", { className: "small", text: "Asked. Proposals will appear below when the assistant has finished." }));
    pollResults();
  }

  // Drafting (X.46.8 / M6.1.a): no text is selected, the input is the section's claim outline (app/section_draft.py).
  async function requestDraft() {
    const previewEl = panelBody.querySelector(".write-assistant-preview");
    try {
      await request(`${documentPath}/draft-suggestion`, { method: "POST" });
    } catch (error) {
      if (previewEl) previewEl.replaceChildren(el("p", { className: "small", text: error.message }));
      return;
    }
    if (previewEl) previewEl.replaceChildren(el("p", { className: "small", text: "Asked for a draft. It will appear below as a suggestion when ready." }));
    pollResults();
  }

  // The task runs in the background, so the proposals are fetched again a few times after a request.
  function pollResults() {
    window.clearInterval(s.pollTimer);
    let tries = 0;
    s.pollTimer = window.setInterval(() => {
      tries += 1;
      mountResults();
      if (tries >= RESULT_POLL_TIMES) window.clearInterval(s.pollTimer);
    }, RESULT_POLL_MS);
  }

  async function mountResults() {
    const host = s.resultsEl;
    if (!host || !host.isConnected || s.tab !== "assistant") return;
    const panel = await suggestionPanel(ctx, {
      targetKind: "document",
      targetId: section.id,
      onChange: mountResults,
      extraActions: insertActions,
    });
    if (s.resultsEl === host) host.replaceChildren(panel);
  }

  function insertActions(suggestion) {
    if (!ctx.canWrite || suggestion.state !== "accepted") return [];
    if (suggestion.action === "clearer_sentence") {
      const insert = el("button", { type: "button", className: "quiet-button", text: "Insert in place of selection" });
      insert.addEventListener("click", () => insertAccepted(suggestion));
      return [insert];
    }
    if (suggestion.action === "draft") {
      const insert = el("button", { type: "button", className: "quiet-button", text: "Insert at cursor" });
      insert.addEventListener("click", () => insertDraftAccepted(suggestion));
      return [insert];
    }
    return [];
  }

  // Replaces the selected text with the accepted wording. The text is labelled ai_edited (editor/provenance.js) and
  // the old wording stays in the previous version, so the record shows what changed and who chose it.
  function insertAccepted(suggestion) {
    const { from, to } = s.view.state.selection;
    if (from === to) return toast("Select the sentence you want to replace first.");
    if (!s.view.state.doc.resolve(from).sameParent(s.view.state.doc.resolve(to))) return toast("Select text within one paragraph.");
    const tr = s.view.state.tr
      .replaceWith(from, to, schema.text(suggestion.accepted_text))
      .setMeta(AI_INSERT_META, { suggestionId: suggestion.id });
    s.view.dispatch(tr);
    s.view.focus();
    toast("Inserted. Saved automatically; the old wording stays in the previous version.");
  }

  // Inserts the accepted draft as a new paragraph at the cursor: there is no existing passage to replace. The text
  // is labelled ai_suggested (editor/provenance.js), not ai_edited, because it is AI wording inserted whole, not an
  // edit of the researcher's own words (Q27: the researcher must still choose to keep it, same as any suggestion).
  function insertDraftAccepted(suggestion) {
    const pos = s.view.state.selection.to;
    const paragraph = schema.nodes.paragraph.create(null, schema.text(suggestion.accepted_text));
    const tr = s.view.state.tr
      .insert(pos, paragraph)
      .setMeta(AI_INSERT_META, { suggestionId: suggestion.id, origin: "ai_suggested" });
    s.view.dispatch(tr);
    s.view.focus();
    toast("Inserted as a new paragraph. Saved automatically.");
  }

  function historyButton(sid) {
    const button = el("button", { type: "button", className: "quiet-button small", text: "History" });
    button.addEventListener("click", () => {
      s.historyFor = sid;
      renderPanel();
    });
    return button;
  }

  async function loadHistory(sid) {
    const target = panelBody.querySelector("#write-history");
    if (!target) return;
    try {
      const events = await request(`${documentPath}/ledger?span_id=${encodeURIComponent(sid)}`);
      target.replaceChildren(
        el("h4", { text: "Changes to this span" }),
        events.length
          ? el("ol", { children: events.map((event) => el("li", { className: "small", text: `${label(event.kind)} · ${event.actor === me ? "you" : "another member"} · ${event.chars_before} → ${event.chars_after} characters · ${formatDate(event.created_at)}` })) })
          : el("p", { className: "muted small", text: "No changes recorded for this span." }),
      );
    } catch (error) {
      target.replaceChildren(el("p", { className: "small", text: error.message }));
    }
  }

  function resolve(sid, paste) {
    s.view.dispatch(resolvePaste(s.view.state, sid, paste));
  }

  async function renderVersions() {
    panelBody.replaceChildren(el("p", { className: "muted small", text: "Loading versions…" }));
    let versions;
    try {
      versions = await request(`${documentPath}/versions`);
    } catch (error) {
      panelBody.replaceChildren(el("p", { className: "small", text: error.message }));
      return;
    }
    const rows = [...versions].reverse().map((version) => {
      const current = version.number === s.base;
      const who = version.saved_by === me ? "you" : "another member";
      const note = version.restored_from_number ? ` · restored from version ${version.restored_from_number}` : "";
      const row = el("li", { children: [el("span", { className: "small", text: `Version ${version.number} · ${who} · ${formatDate(version.created_at)}${note}${current ? " · current" : ""}` })] });
      if (!current && ctx.canWrite) {
        const restore = el("button", { type: "button", className: "quiet-button small", text: "Restore" });
        restore.addEventListener("click", () => restoreVersion(version.number));
        row.append(restore);
      }
      return row;
    });
    panelBody.replaceChildren(
      el("h3", { text: "Versions" }),
      el("p", { className: "muted small", text: "Restoring copies an earlier version in as a new version. Nothing is deleted." }),
      rows.length ? el("ol", { className: "write-versions", children: rows }) : el("p", { className: "muted small", text: "No saved versions yet." }),
    );
  }

  async function restoreVersion(number) {
    if (s.dirty) await save();
    if (s.dirty) {
      toast("Save your changes before restoring, then try again.");
      return;
    }
    try {
      const result = await request(`${documentPath}/versions/${number}/restore`, {
        method: "POST", body: JSON.stringify({ base_number: s.base }),
      });
      replaceText(PM.model.Node.fromJSON(schema, result.version.content_json), { dirty: false });
      s.base = result.version.number;
      s.latestId = result.version.id;
      s.latestText = result.version.content_text;
      await clearDraft(section.id);
      setStatus(`Restored version ${number} as version ${s.base}.`);
      renderPanel();
    } catch (error) {
      toast(error.status === 409 ? "This section changed while you were restoring. Reload it and try again." : error.message);
    }
  }

  setStatus(s.base ? `Saved · version ${s.base}` : "Nothing saved yet");
  renderPanel();
  return s;
}

function safeDoc(json) {
  try {
    return PM.model.Node.fromJSON(schema, json);
  } catch {
    return null;
  }
}

function excerpt(text, length = 80) {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > length ? `${flat.slice(0, length - 1)}…` : flat;
}

export default { render };
