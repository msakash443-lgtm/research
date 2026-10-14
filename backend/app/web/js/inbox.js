// Inbox and decision log (X.31.2.1): triage captured notes, and read the decisions made in each project.
// Rendering only. What to show is decided in inboxLogic.js; the server checks every action again.

import { $, el, emptyCard, formatDate, saveOnce, toast } from "./dom.js";
import { request } from "./api.js";
import { errorCard } from "./steps/common.js";
import {
  decisionRows,
  INBOX_TABS,
  loadDismissed,
  newestRender,
  parseInboxRoute,
  promoteDefaults,
  promotedInto,
  rememberDismissed,
  suggestionChips,
  writableProjects,
} from "./inboxLogic.js";

export { INBOX_TABS };

const TAB_LABELS = { inbox: "Inbox", filed: "Filed", archived: "Archived", decisions: "Decisions" };
const PROMOTE_KINDS = [
  ["idea", "Idea"],
  ["gap", "Gap"],
  ["hypothesis", "Hypothesis"],
  ["claim", "Claim"],
  ["memo", "Memo"],
  ["decision", "Decision"],
];

// The signed-in user's id (set by renderInbox): needed to tell which projects the caller may file into.
let viewerId = null;

// Dismissed chips are saved in this browser (see inboxLogic.js). Reading localStorage can throw when site data is
// blocked, so the helpers take it as an argument and handle that themselves.
function browserStorage() {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

// `projectId` (optional) opens the decisions tab on that project: the per-project decisions page.
const renders = newestRender();

export async function renderInbox(root, tab, userId, projectId = null) {
  viewerId = userId;
  const token = renders.start();
  root.replaceChildren(el("div", { className: "inbox-loading muted", text: "Loading…" }));
  try {
    const projects = await request("/api/projects");
    const body = tab === "decisions" ? await decisionsTab(projects, projectId) : await notesTab(tab, projects, userId);
    if (renders.isCurrent(token)) root.replaceChildren(header(tab), body);
  } catch (error) {
    if (renders.isCurrent(token)) root.replaceChildren(header(tab), errorCard(error.message));
  }
}

function header(active) {
  const tabs = el("nav", { className: "inbox-tabs", attrs: { "aria-label": "Inbox sections" } });
  for (const tab of INBOX_TABS) {
    const button = el("a", {
      className: `tab-button${tab === active ? " active" : ""}`,
      text: TAB_LABELS[tab],
      href: `#/inbox/${tab}`,
      attrs: tab === active ? { "aria-current": "page" } : {},
    });
    tabs.append(button);
  }
  return el("div", {
    className: "inbox-header",
    children: [el("p", { className: "eyebrow", text: "CAPTURED" }), el("h1", { text: "Inbox" }), tabs],
  });
}

// Writable projects, and the members of every project (to tell roles apart). One request per project.
async function writableFor(projects, userId) {
  const members = {};
  await Promise.all(
    projects.map(async (project) => {
      members[project.id] = await request(`/api/projects/${project.id}/members`);
    })
  );
  return writableProjects(projects, members, userId);
}

async function notesTab(tab, projects, userId) {
  const notes = await request(`/api/notes?status=${tab}`);
  if (notes.length === 0) {
    return emptyCard(
      tab === "inbox" ? "Nothing to sort" : `No ${TAB_LABELS[tab].toLowerCase()} notes`,
      tab === "inbox" ? "New captures appear here. Press Capture, or Alt+Shift+N." : "Notes move here when you file or archive them."
    );
  }
  const writable = await writableFor(projects, userId);
  const titles = new Map(projects.map((project) => [project.id, project.title]));
  const promoted = await promotedNotes(notes);
  const list = el("div", { className: "inbox-list" });
  list.append(...notes.map((note) => noteCard(note, { writable, titles, promoted, tab })));
  return list;
}

// Context items for every project that has a filed note, keyed by the note they came from.
async function promotedNotes(notes) {
  const projectIds = [...new Set(notes.map((note) => note.project_id).filter(Boolean))];
  const items = [];
  for (const projectId of projectIds) {
    items.push(...(await request(`/api/projects/${projectId}/context`)));
  }
  return promotedInto(items);
}

function noteCard(note, ctx) {
  const card = el("article", { className: "note-row inbox-note", attrs: { "data-note-id": note.id } });
  const kindText = note.kind === "clip" ? "Web link" : "Note";
  card.append(
    el("div", {
      className: "note-row-head",
      children: [
        el("span", { className: "note-kind-badge", text: kindText }),
        el("span", { className: "note-status-badge", text: note.status }),
      ],
    })
  );
  if (note.kind === "clip" && note.quoted_text) {
    card.append(el("blockquote", { className: "note-quote", text: note.quoted_text }));
  }
  if (note.body) card.append(el("p", { className: "note-row-body", text: note.body }));
  if (note.source_url) card.append(el("a", { href: note.source_url, target: "_blank", rel: "noopener", text: note.source_url }));
  const meta = [`captured ${formatDate(note.captured_at)}`];
  if (note.project_id) meta.push(`filed to ${ctx.titles.get(note.project_id) || "a project"}`);
  card.append(el("p", { className: "muted small", text: meta.join(" · ") }));

  const promotion = ctx.promoted.get(note.id);
  if (promotion) {
    card.append(el("p", { className: "small promoted-link", text: `Promoted to: ${promotion.kind} — ${promotion.content}` }));
  }

  if (note.status !== "archived") card.append(suggestionRow(note, ctx));
  card.append(actionRow(note, ctx));
  return card;
}

function suggestionRow(note, ctx) {
  const row = el("div", { className: "chip-row", attrs: { "aria-label": "Suggestions" } });
  request(`/api/notes/${note.id}/suggestions`)
    .then((suggestions) => {
      const writableIds = new Set(ctx.writable.map((project) => project.id));
      const storage = browserStorage();
      for (const chip of suggestionChips(suggestions, loadDismissed(storage, note.id))) {
        const canFile = chip.actionable && writableIds.has(chip.projectId);
        const text = el("span", { text: chip.text });
        const dismiss = el("button", { className: "quiet-button small chip-dismiss", type: "button", text: "×", attrs: { "aria-label": `Dismiss suggestion ${chip.text}` } });
        dismiss.addEventListener("click", () => {
          rememberDismissed(storage, note.id, chip.key);
          row.replaceWith(suggestionRow(note, ctx));
        });
        const chipNode = el("span", { className: `chip${canFile ? " chip-action" : ""}`, children: [text] });
        if (canFile) {
          const projectTitle = ctx.titles.get(chip.projectId) || "its project";
          chipNode.title = `File this note to ${projectTitle}`;
          text.addEventListener("click", () => fileNote(note, chip.projectId));
          text.setAttribute("role", "button");
          text.tabIndex = 0;
          text.addEventListener("keydown", (event) => {
            if (event.key === "Enter" || event.key === " ") fileNote(note, chip.projectId);
          });
        }
        chipNode.append(dismiss);
        row.append(chipNode);
      }
    })
    .catch(() => {});
  return row;
}

function actionRow(note, ctx) {
  const actions = el("div", { className: "inbox-actions" });
  if (note.status !== "archived") {
    if (ctx.writable.length) {
      const select = el("select", { attrs: { "aria-label": "Project to file this note under" } });
      for (const project of ctx.writable) select.append(el("option", { text: project.title, attrs: { value: project.id } }));
      const fileButton = el("button", { className: "quiet-button small", type: "button", text: note.status === "filed" ? "Move to" : "File to project" });
      fileButton.addEventListener("click", () => fileNote(note, select.value));
      actions.append(select, fileButton);
    } else {
      actions.append(el("span", { className: "muted small", text: "No project you can write to yet." }));
    }
    if (note.status === "filed") {
      const promoteButton = el("button", { className: "quiet-button small", type: "button", text: "Promote ▸", attrs: { "aria-expanded": "false" } });
      const form = promoteForm(note);
      promoteButton.addEventListener("click", () => {
        const open = form.hidden;
        form.hidden = !open;
        promoteButton.setAttribute("aria-expanded", String(open));
      });
      actions.append(promoteButton);
      actions.append(form);
    }
    const archiveButton = el("button", { className: "quiet-button small", type: "button", text: "Archive" });
    archiveButton.addEventListener("click", () => archiveNote(note));
    actions.append(archiveButton);
  }
  return actions;
}

async function fileNote(note, projectId) {
  try {
    await request(`/api/notes/${note.id}/file`, { method: "POST", body: JSON.stringify({ project_id: projectId }) });
    toast("Filed.");
    await rerender();
  } catch (error) {
    toast(error.message);
  }
}

async function archiveNote(note) {
  try {
    await request(`/api/notes/${note.id}/archive`, { method: "POST" });
    toast("Archived.");
    await rerender();
  } catch (error) {
    toast(error.message);
  }
}

function promoteForm(note) {
  const defaults = promoteDefaults(note);
  const form = el("form", { className: "promote-form" });
  form.hidden = true;
  const kind = el("select", { attrs: { name: "kind", "aria-label": "Kind of item" } });
  for (const [value, text] of PROMOTE_KINDS) kind.append(el("option", { text, attrs: { value } }));
  const content = el("textarea", { attrs: { name: "content", maxlength: "20000", required: "", "aria-label": "Wording" } });
  content.value = defaults.content;
  const rationale = el("textarea", { attrs: { name: "rationale", maxlength: "20000", placeholder: "Why (optional)", "aria-label": "Rationale" } });
  const related = el("select", { attrs: { name: "related_item_id", "aria-label": "Item this decision concerns" } });
  const relatedLabel = el("label", { className: "related-field", children: [el("span", { text: "Concerns (optional)" }), related] });
  relatedLabel.hidden = true;
  kind.addEventListener("change", () => {
    relatedLabel.hidden = kind.value !== "decision";
  });
  const submit = el("button", { type: "submit", text: "Promote" });
  form.append(
    el("label", { children: [el("span", { text: "Kind" }), kind] }),
    el("label", { children: [el("span", { text: "Wording" }), content] }),
    el("label", { children: [el("span", { text: "Rationale" }), rationale] }),
    relatedLabel,
    el("div", { className: "dialog-actions", children: [submit] })
  );
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    saveOnce(form, async () => {
      const relatedId = kind.value === "decision" && related.value ? related.value : null;
      try {
        await request(`/api/notes/${note.id}/promote`, {
          method: "POST",
          body: JSON.stringify({
            kind: kind.value,
            content: content.value,
            rationale: rationale.value.trim() || null,
            related_item_id: relatedId,
          }),
        });
        toast(`Promoted to ${kind.value}.`);
        await rerender();
      } catch (error) {
        toast(error.message);
      }
    });
  });
  // The "Concerns" picker lists the note's project items, loaded when the form is first opened.
  let loaded = false;
  form.addEventListener("focusin", async () => {
    if (loaded || !note.project_id) return;
    loaded = true;
    try {
      const items = await request(`/api/projects/${note.project_id}/context`);
      related.append(el("option", { text: "None", attrs: { value: "" } }));
      for (const item of items) related.append(el("option", { text: `${item.kind}: ${item.content}`, attrs: { value: item.id } }));
    } catch (error) {
      toast(error.message);
    }
  });
  return form;
}

async function decisionsTab(projects, projectId) {
  const wrapper = el("div", { className: "decisions" });
  if (projects.length === 0) {
    wrapper.append(emptyCard("No projects yet", "Decisions are recorded in a project. Create one first."));
    return wrapper;
  }
  if (projectId && !projects.some((project) => project.id === projectId)) {
    wrapper.append(emptyCard("Project not found", "It may have been removed, or you may not have access to it."));
    return wrapper;
  }
  const select = el("select", { attrs: { "aria-label": "Project" } });
  for (const project of projects) select.append(el("option", { text: project.title, attrs: { value: project.id } }));
  if (projectId) select.value = projectId;
  const list = el("div", { className: "decision-list" });
  const load = async () => {
    list.replaceChildren(el("p", { className: "muted", text: "Loading…" }));
    try {
      const items = await request(`/api/projects/${select.value}/context`);
      const rows = decisionRows(items);
      if (rows.length === 0) {
        list.replaceChildren(emptyCard("No decisions yet", "Promote a note to a decision, or add one from the project's research context."));
        return;
      }
      list.replaceChildren(...rows.map(decisionCard));
    } catch (error) {
      list.replaceChildren(errorCard(error.message));
    }
  };
  select.addEventListener("change", () => {
    // Keep the address in step with the page, so the project's decisions can be bookmarked or shared.
    window.history.replaceState(null, "", `#/inbox/decisions/${select.value}`);
    load();
  });
  wrapper.append(el("label", { className: "decision-project", children: [el("span", { text: "Project" }), select] }), list);
  load();
  return wrapper;
}

function decisionCard(row) {
  const card = el("article", { className: "note-row decision-row" });
  card.append(el("p", { className: "decision-content", text: row.content }));
  if (row.concerns) card.append(el("p", { className: "small", text: `Concerns: ${row.concerns}` }));
  if (row.rationale) card.append(el("p", { className: "muted small", text: `Why: ${row.rationale}` }));
  card.append(el("p", { className: "muted small", text: formatDate(row.createdAt) }));
  return card;
}

async function rerender() {
  const route = parseInboxRoute(window.location.hash) || { tab: "inbox", projectId: null };
  await renderInbox($("#inbox-view"), route.tab, viewerId, route.projectId);
}
