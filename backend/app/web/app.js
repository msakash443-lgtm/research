import { $, el, toast, saveOnce, label } from "./js/dom.js";
import { errorCard } from "./js/steps/common.js";
import { request, projectApi } from "./js/api.js";
import { STAGES, STEPS, GROUPS, GUIDES, STEP_IDS, stepStatus } from "./js/workflow.js";
import { renderMemory } from "./js/memory.js";

import overview from "./js/steps/overview.js";
import scope from "./js/steps/scope.js";
import criteria from "./js/steps/criteria.js";
import search from "./js/steps/search.js";
import results from "./js/steps/results.js";
import sources from "./js/steps/sources.js";
import ask from "./js/steps/ask.js";
import approvals from "./js/steps/approvals.js";
import team from "./js/steps/team.js";
import activity from "./js/steps/activity.js";

const STEP_MODULES = { overview, scope, criteria, search, results, sources, ask, approvals, team, activity };

const state = {
  user: null,
  projects: [],
  activeProject: null,
  role: null,
  currentStep: "overview",
  summary: {},
  cache: { profiles: null, connectors: null },
  pollTimer: null,
};

function buildCtx() {
  const role = state.role;
  return {
    project: state.activeProject,
    user: state.user,
    role,
    canWrite: role === "owner" || role === "co_author",
    isOwner: role === "owner",
    summary: state.summary,
    cache: state.cache,
    refresh,
    navigate,
  };
}

function navigate(stepId) {
  if (!state.activeProject) return;
  window.location.hash = `#/p/${state.activeProject.id}/${stepId}`;
}

function parseHash() {
  const match = window.location.hash.match(/^#\/p\/([^/]+)\/([^/]+)$/);
  if (!match) return null;
  return { projectId: match[1], stepId: match[2] };
}

function memberRole() {
  const members = state.summary.members || [];
  const mine = members.find((m) => m.user_id === state.user?.id);
  return mine?.role || null;
}

// On phones/tablets the project rail and research memory are off-canvas drawers opened from the
// top bar; on wider screens the memory sidebar can instead be collapsed to a two-column layout.
// Only one drawer is open at a time; the rest of the page is inert while one is open.
function setDrawer(name) {
  const wasOpen = document.body.classList.contains("nav-open") ? "nav" : document.body.classList.contains("memory-open") ? "memory" : null;
  document.body.classList.remove("nav-open", "memory-open");
  $(".topbar").inert = false;
  $("#step-view").inert = false;
  $("#nav-toggle").setAttribute("aria-expanded", String(name === "nav"));
  $("#memory-toggle").setAttribute("aria-expanded", String(name === "memory"));
  if (name) {
    document.body.classList.add(`${name}-open`);
    $(".topbar").inert = true;
    $("#step-view").inert = true;
    (name === "nav" ? $("#new-project") : $("#memory-sidebar")).focus();
  } else if (wasOpen) {
    (wasOpen === "nav" ? $("#nav-toggle") : $("#memory-toggle")).focus();
  }
}

function stepLink(step) {
  const { status, count } = stepStatus(step.id, state.summary);
  const classes = ["step-link", `step-status-${status}`];
  if (state.currentStep === step.id) classes.push("active");
  const a = el("a", { className: classes.join(" "), href: `#/p/${state.activeProject.id}/${step.id}` });
  const glyph = status === "done" ? "✓" : status === "attention" ? "!" : status === "todo" ? "○" : "";
  a.append(el("span", { children: [el("span", { className: "step-glyph", text: glyph }), document.createTextNode(` ${step.title}`)] }));
  if (count) a.append(el("span", { className: "step-count", text: String(count) }));
  a.addEventListener("click", () => setDrawer(null));
  return a;
}

function renderProjectList() {
  const list = $("#project-list");
  const focusedId = list.contains(document.activeElement) ? document.activeElement.dataset.projectId : null;
  const items = state.projects.map((project) => {
    const active = state.activeProject?.id === project.id;
    const a = el("a", { className: `project-item${active ? " active" : ""}`, text: project.title, href: `#/p/${project.id}/overview` });
    a.dataset.projectId = project.id;
    a.addEventListener("click", () => setDrawer(null));
    return a;
  });
  list.replaceChildren(...items);
  const toFocus = items.find((item) => item.dataset.projectId === focusedId);
  if (toFocus) toFocus.focus();
}

function renderShell() {
  renderProjectList();
  $("#idea-button").hidden = !(state.activeProject && (state.role === "owner" || state.role === "co_author"));
  $("#memory-toggle").hidden = !state.activeProject;
  if (!state.activeProject) {
    $("#role-chip").hidden = true;
    return;
  }
  $("#crumb").textContent = state.activeProject.title;
  $("#role-chip").hidden = !state.role;
  if (state.role) {
    $("#role-chip").textContent = label(state.role);
    $("#role-chip").title = ["supervisor", "reviewer"].includes(state.role)
      ? "You can view everything; supervisors approve gates. Editing is for owners and co-authors."
      : "";
  }
  const stageName = state.summary.stage?.stage;
  const idx = STAGES.findIndex(([name]) => name === stageName);
  $("#progress-block").hidden = false;
  $("#stage-progress-label").textContent = `Stage ${idx + 1} of ${STAGES.length} · ${label(stageName || "")}`;
  $("#stage-progress").value = idx >= 0 ? idx : 0;

  const rail = $("#step-rail");
  rail.replaceChildren(stepLink(STEPS.find((s) => s.id === "overview")));
  GROUPS.forEach((group) => {
    rail.append(el("p", { className: "step-group-label", text: group.toUpperCase() }));
    STEPS.filter((s) => s.group === group).forEach((step) => rail.append(stepLink(step)));
  });

  const laterList = $("#later-stages-list");
  laterList.replaceChildren();
  const fromIndex = STAGES.findIndex(([name]) => name === "retrieved");
  STAGES.slice(fromIndex).forEach(([name, gate]) => {
    laterList.append(el("li", { text: gate ? `${label(name)} (${gate})` : label(name) }));
  });
  $("#later-stages").hidden = false;
}

const STEP_DEPENDENCIES = {
  overview: ["stage", "gates"],
  scope: ["context", "gates", "stage"],
  criteria: ["criteria", "seeds"],
  search: ["searches", "gates", "stage"],
  results: ["prisma", "knownItems", "seeds"],
  sources: ["sources"],
  ask: ["runs"],
  approvals: ["stage", "gates", "staleArtifacts"],
  team: ["members"],
  activity: [],
};

async function renderStep(stepId) {
  const step = STEPS.find((s) => s.id === stepId) || STEPS[0];
  $("#step-group").textContent = step.group || "Project";
  $("#step-title").textContent = step.title;
  $("#step-purpose").textContent = GUIDES[stepId]?.what || "";
  const body = $("#step-body");
  const failedKey = (STEP_DEPENDENCIES[stepId] || []).find((key) => state.summary.errors?.[key]);
  if (failedKey) {
    body.replaceChildren(errorCard(state.summary.errors[failedKey], refresh));
  } else {
    await STEP_MODULES[stepId].render(body, buildCtx());
  }
  $("#memory-sidebar").hidden = false;
  renderMemory($("#memory-body"), buildCtx());
  updatePoll();
}

async function loadSummary() {
  if (!state.activeProject) return;
  const projectId = state.activeProject.id;
  const endpoints = {
    stage: "/stage", gates: "/gates", context: "/context", criteria: "/criteria", seeds: "/seeds",
    searches: "/searches", sources: "/sources", runs: "/research-runs", members: "/members",
    knownItems: "/known-items", prisma: "/prisma", staleArtifacts: "/artifacts?status=stale",
  };
  const keys = Object.keys(endpoints);
  const settled = await Promise.allSettled(keys.map((key) => request(projectApi(projectId, endpoints[key]))));
  if (!state.activeProject || state.activeProject.id !== projectId) return;
  const summary = { errors: {} };
  keys.forEach((key, index) => {
    const result = settled[index];
    if (result.status === "fulfilled") summary[key] = result.value;
    else {
      summary[key] = key === "staleArtifacts" || key === "gates" || key === "context" || key === "seeds" || key === "sources" || key === "members" ? [] : null;
      summary.errors[key] = result.reason.message;
    }
  });
  state.summary = summary;
}

async function refresh() {
  await loadSummary();
  state.role = memberRole();
  renderShell();
  await renderStep(state.currentStep);
}

function shouldPoll() {
  const runs = state.summary.runs || [];
  const activeRun = runs.some((r) => r.status === "queued" || r.status === "running");
  const pending = state.summary.searches?.pending || [];
  const activeSearch = pending.some((p) => p.status === "queued" || p.status === "running");
  return activeRun || activeSearch;
}

function updatePoll() {
  window.clearTimeout(state.pollTimer);
  if (shouldPoll()) state.pollTimer = window.setTimeout(pollTick, 2500);
}

async function pollTick() {
  const projectId = state.activeProject?.id;
  const wasActive = shouldPoll();
  await loadSummary();
  // The project may have changed or been cleared (logout, switch) while this tick was in flight;
  // the new state already scheduled its own poll, so this stale tick must not touch the DOM.
  if (state.activeProject?.id !== projectId) return;
  state.role = memberRole();
  renderShell();
  if (wasActive && !shouldPoll()) {
    // Something just finished (e.g. a search or a run that adds sources): a full re-render
    // picks up the new data everywhere, not just the polled list.
    await renderStep(state.currentStep);
    return;
  }
  const mod = STEP_MODULES[state.currentStep];
  if (mod.update) mod.update($("#step-body"), buildCtx());
  renderMemory($("#memory-body"), buildCtx());
  updatePoll();
}

async function setStep(stepId, updateHash) {
  state.currentStep = STEP_IDS.has(stepId) ? stepId : "overview";
  if (updateHash) window.location.hash = `#/p/${state.activeProject.id}/${state.currentStep}`;
  renderShell();
  await renderStep(state.currentStep);
  setDrawer(null);
}

function showEmpty() {
  state.activeProject = null;
  state.role = null;
  state.summary = {};
  window.clearTimeout(state.pollTimer);
  $("#empty-state").hidden = false;
  $("#step-content").hidden = true;
  $("#progress-block").hidden = true;
  $("#step-rail").replaceChildren();
  $("#later-stages").hidden = true;
  $("#memory-sidebar").hidden = true;
  $("#crumb").textContent = "";
  $("#role-chip").hidden = true;
  $("#idea-button").hidden = true;
  $("#memory-toggle").hidden = true;
  renderProjectList();
}

async function selectProject(id, stepId = "overview") {
  window.clearTimeout(state.pollTimer);
  try {
    state.activeProject = await request(`/api/projects/${id}`);
  } catch (error) {
    toast(error.message);
    if (state.projects.some((p) => p.id !== id) && state.projects.length) {
      await selectProject(state.projects.find((p) => p.id !== id).id, "overview");
    } else {
      showEmpty();
    }
    return;
  }
  $("#empty-state").hidden = true;
  $("#step-content").hidden = false;
  await loadSummary();
  state.role = memberRole();
  await setStep(stepId, false);
}

async function loadProjects() {
  state.projects = await request("/api/projects");
  renderProjectList();
  const parsed = parseHash();
  if (parsed && state.projects.some((p) => p.id === parsed.projectId)) {
    await selectProject(parsed.projectId, parsed.stepId);
  } else if (state.projects.length) {
    await selectProject(state.projects[0].id, "overview");
  } else {
    showEmpty();
  }
}

window.addEventListener("hashchange", () => {
  const parsed = parseHash();
  if (!parsed || !state.user) return;
  if (!state.activeProject || state.activeProject.id !== parsed.projectId) {
    if (state.projects.some((p) => p.id === parsed.projectId)) selectProject(parsed.projectId, parsed.stepId);
    return;
  }
  if (parsed.stepId !== state.currentStep) setStep(parsed.stepId, false);
});

if (localStorage.getItem("ra.memoryCollapsed") === "1") document.body.classList.add("memory-collapsed");

$("#nav-toggle").addEventListener("click", () => setDrawer(document.body.classList.contains("nav-open") ? null : "nav"));
$("#memory-toggle").addEventListener("click", () => {
  if (window.matchMedia("(min-width: 1280px)").matches) {
    document.body.classList.toggle("memory-collapsed");
    localStorage.setItem("ra.memoryCollapsed", document.body.classList.contains("memory-collapsed") ? "1" : "0");
  } else {
    setDrawer(document.body.classList.contains("memory-open") ? null : "memory");
  }
});
$("#nav-backdrop").addEventListener("click", () => setDrawer(null));
$("#memory-backdrop").addEventListener("click", () => setDrawer(null));
window.matchMedia("(max-width: 760px)").addEventListener("change", () => setDrawer(null));
window.matchMedia("(max-width: 1279px)").addEventListener("change", () => setDrawer(null));

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    if (document.body.classList.contains("nav-open") || document.body.classList.contains("memory-open")) setDrawer(null);
    return;
  }
  if (event.altKey && event.code === "KeyI" && state.activeProject && !document.querySelector("dialog[open]")) {
    event.preventDefault();
    $("#idea-popover").showPopover();
    $("#idea-form textarea").focus();
  }
});

$("#guide-popover").addEventListener("beforetoggle", (event) => {
  if (event.newState !== "open") return;
  const guide = GUIDES[state.currentStep] || {};
  $("#guide-what").textContent = guide.what || "";
  $("#guide-why").textContent = guide.why || "";
  $("#guide-approval").textContent = guide.approval || "";
});

$("#new-project").addEventListener("click", () => {
  setDrawer(null);
  $("#project-dialog").showModal();
});
$("#empty-new-project").addEventListener("click", () => $("#project-dialog").showModal());
document.querySelectorAll("[data-close]").forEach((button) => button.addEventListener("click", () => button.closest("dialog").close()));

$("#project-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  saveOnce(form, async () => {
    const data = Object.fromEntries(new FormData(form));
    try {
      const project = await request("/api/projects", { method: "POST", body: JSON.stringify(data) });
      form.reset();
      $("#project-dialog").close();
      state.projects = await request("/api/projects");
      toast("Project created.");
      window.location.hash = `#/p/${project.id}/overview`;
    } catch (error) {
      toast(error.message);
    }
  });
});

$("#context-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  saveOnce(form, async () => {
    const data = Object.fromEntries(new FormData(form));
    if (!data.rationale) delete data.rationale;
    try {
      await request(projectApi(state.activeProject.id, "/context"), { method: "POST", body: JSON.stringify(data) });
      form.reset();
      $("#context-dialog").close();
      toast("Research context saved.");
      await refresh();
    } catch (error) {
      toast(error.message);
    }
  });
});

$("#source-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const data = Object.fromEntries(new FormData(form));
  ["url", "year", "evidence_excerpt", "locator"].forEach((key) => {
    if (!data[key]) delete data[key];
  });
  if (data.year) data.year = Number(data.year);
  saveOnce(form, async () => {
    try {
      await request(projectApi(state.activeProject.id, "/sources"), { method: "POST", body: JSON.stringify(data) });
      form.reset();
      $("#source-dialog").close();
      toast("Evidence source saved.");
      await refresh();
    } catch (error) {
      toast(error.message);
    }
  });
});

$("#idea-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  saveOnce(form, async () => {
    const content = form.elements.content.value;
    try {
      await request(projectApi(state.activeProject.id, "/context"), { method: "POST", body: JSON.stringify({ kind: "idea", content }) });
      form.reset();
      $("#idea-popover").hidePopover();
      toast("Idea saved to research memory.");
      await refresh();
    } catch (error) {
      toast(error.message);
    }
  });
});

$("#reject-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const dialog = $("#reject-dialog");
  const code = dialog.dataset.gateCode;
  saveOnce(form, async () => {
    const note = form.elements.note.value.trim();
    try {
      await request(projectApi(state.activeProject.id, `/gates/${code}/reject`), { method: "POST", body: JSON.stringify({ note }) });
      form.reset();
      dialog.close();
      toast(`${code} rejected.`);
      await refresh();
    } catch (error) {
      toast(error.message);
    }
  });
});

async function authenticate() {
  try {
    state.user = await request("/api/auth/me");
  } catch {
    state.user = null;
  }
  if (state.user) {
    $("#auth-screen").hidden = true;
    $("#workspace").hidden = false;
    $("#account").hidden = false;
    $("#nav-toggle").hidden = false;
    $("#account-name").textContent = state.user.display_name || state.user.email;
    await loadProjects();
    return;
  }
  const methods = await request("/api/auth/methods");
  $("#auth-screen").hidden = false;
  $("#workspace").hidden = true;
  $("#google-login").hidden = !methods.google_configured;
  $("#development-login").hidden = !methods.development_login;
  $("#auth-note").textContent = methods.google_configured
    ? "Sign in securely to access your research."
    : "Google login can be enabled later. This local account is only available while developing the app.";
}

$("#development-login").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  try {
    await request("/api/auth/development/login", { method: "POST", body: JSON.stringify(Object.fromEntries(form)) });
    toast("Development account ready.");
    await authenticate();
  } catch (error) {
    toast(error.message);
  }
});

$("#logout").addEventListener("click", async () => {
  await request("/api/auth/logout", { method: "POST" });
  state.user = null;
  showEmpty();
  setDrawer(null);
  $("#account").hidden = true;
  $("#nav-toggle").hidden = true;
  authenticate();
});

authenticate().catch((error) => toast(error.message));
