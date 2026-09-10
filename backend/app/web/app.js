const state = { user: null, projects: [], activeProject: null, pollTimer: null };
const $ = (selector) => document.querySelector(selector);

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (response.status === 204) return null;
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || "Something went wrong.");
  return body;
}

function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.add("show");
  window.setTimeout(() => node.classList.remove("show"), 3200);
}

function label(value) { return String(value).replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase()); }

function el(tag, options = {}) {
  const node = document.createElement(tag);
  if (options.className) node.className = options.className;
  if (options.text !== undefined) node.textContent = options.text;
  if (options.type) node.type = options.type;
  if (options.href) node.href = options.href;
  if (options.target) node.target = options.target;
  if (options.rel) node.rel = options.rel;
  if (options.title) node.title = options.title;
  return node;
}

function emptyCard(title, description) {
  const card = el("div", { className: "empty-card" });
  card.append(el("h3", { text: title }), el("p", { text: description }));
  return card;
}

function formatDate(value) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? "" : date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

async function loadProjects() {
  state.projects = await request("/api/projects");
  renderProjects();
  if (!state.activeProject && state.projects.length) await selectProject(state.projects[0].id);
  if (!state.projects.length) showEmpty();
}

function renderProjects() {
  const list = $("#project-list");
  list.replaceChildren(...state.projects.map((project) => {
    const button = el("button", { className: `project-item ${state.activeProject?.id === project.id ? "active" : ""}`, text: project.title });
    button.onclick = () => selectProject(project.id);
    return button;
  }));
}

function showEmpty() {
  state.activeProject = null;
  window.clearTimeout(state.pollTimer);
  $("#empty-state").hidden = false;
  $("#project-view").hidden = true;
  renderProjects();
}

async function selectProject(id) {
  window.clearTimeout(state.pollTimer);
  state.activeProject = await request(`/api/projects/${id}`);
  $("#empty-state").hidden = true;
  $("#project-view").hidden = false;
  $("#project-title").textContent = state.activeProject.title;
  $("#project-description").textContent = state.activeProject.description || "No description yet.";
  renderProjects();
  await Promise.all([loadContext(), loadSources(), loadClaims(), loadResearchRuns(), loadDatasets(), loadLiteratureDocuments()]);
}

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------
async function loadContext() {
  const items = await request(`/api/projects/${state.activeProject.id}/context`);
  const list = $("#context-list");
  if (!items.length) {
    list.replaceChildren(emptyCard("Capture your first research decision", "Add a question, objective, or methodological choice to make this project easier to continue later."));
    return;
  }
  list.replaceChildren(...items.map((item) => {
    const card = el("article", { className: "context-card" });
    const heading = el("div", { className: "card-heading" });
    heading.append(el("span", { className: "context-kind", text: label(item.kind) }));
    const deleteBtn = el("button", { className: "icon-button", text: "×", title: "Delete" });
    deleteBtn.onclick = async () => {
      if (!confirm("Delete this context item?")) return;
      try {
        await request(`/api/projects/${state.activeProject.id}/context/${item.id}`, { method: "DELETE" });
        await loadContext();
        toast("Context item removed.");
      } catch (error) { toast(error.message); }
    };
    heading.append(deleteBtn);
    card.append(heading, el("h3", { text: item.content }));
    card.append(el("p", { text: item.rationale ? `Reason: ${item.rationale}` : "Saved to this project's structured research memory." }));
    return card;
  }));
}

// ---------------------------------------------------------------------------
// Sources
// ---------------------------------------------------------------------------
async function loadSources() {
  const sources = await request(`/api/projects/${state.activeProject.id}/sources`);
  const list = $("#source-list");
  if (!sources.length) {
    list.replaceChildren(emptyCard("No evidence sources yet", "Add a primary source and a short excerpt, statistic, or data note before asking the agent for a synthesis."));
    return;
  }

  list.replaceChildren(...sources.map((source) => {
    const card = el("article", { className: "source-card" });
    const meta = [label(source.source_type), source.year].filter(Boolean).join(" · ");
    const heading = el("div", { className: "card-heading" });
    heading.append(el("span", { className: "context-kind", text: meta || "Source" }));
    const deleteBtn = el("button", { className: "icon-button", text: "×", title: "Delete source" });
    deleteBtn.onclick = async () => {
      if (!confirm(`Delete "${source.title}"?`)) return;
      try {
        await request(`/api/projects/${state.activeProject.id}/sources/${source.id}`, { method: "DELETE" });
        await loadSources();
        toast("Source removed.");
      } catch (error) { toast(error.message); }
    };
    heading.append(deleteBtn);
    card.append(heading);
    if (source.url) {
      card.append(el("a", { className: "source-title", text: source.title, href: source.url, target: "_blank", rel: "noopener noreferrer" }));
    } else {
      card.append(el("h3", { text: source.title }));
    }

    if (source.evidence_excerpt) {
      card.append(el("p", { className: "evidence-excerpt", text: source.evidence_excerpt }));
      if (source.excerpt_locator) card.append(el("p", { className: "source-locator", text: `Location: ${source.excerpt_locator}` }));
    } else {
      card.append(el("p", { className: "source-locator", text: "No evidence excerpt saved yet; the agent will not use this source for factual claims." }));
    }

    return card;
  }));
}

function renderSourceResults(sources) {
  const list = $("#source-search-results");
  const style = $("#source-search-form select[name='citation_style']").value;
  if (!sources.length) {
    list.replaceChildren(emptyCard("No matching articles", "Try another title, author, DOI, keyword, or evidence phrase."));
    return;
  }
  list.replaceChildren(...sources.map((source) => {
    const card = el("article", { className: "source-card" });
    card.append(el("h3", { text: source.title }));
    card.append(el("p", { className: "source-locator", text: style === "MLA" ? source.mla_citation : source.apa_citation }));
    card.append(el("p", { className: "source-locator", text: `Format: ${style}` }));
    if (source.doi) card.append(el("p", { className: "source-locator", text: `DOI: ${source.doi}` }));
    if (source.evidence_excerpt) card.append(el("p", { className: "evidence-excerpt", text: source.evidence_excerpt }));
    return card;
  }));
}

async function loadDatasets() {
  const datasets = await request(`/api/projects/${state.activeProject.id}/analysis/datasets`);
  const select = $("#dataset-select");
  select.replaceChildren(...datasets.map((dataset) => {
    const option = el("option", { text: `${dataset.name} (${dataset.row_count} rows)` });
    option.value = dataset.id;
    return option;
  }));
  if (!datasets.length) select.append(el("option", { text: "Upload a dataset first" }));
}

async function loadLiteratureDocuments() {
  const documents = await request(`/api/projects/${state.activeProject.id}/literature-review/documents`);
  const select = $("#literature-document");
  select.replaceChildren(...documents.map((document) => {
    const option = el("option", { text: `${document.filename} (${document.page_count} pages)` });
    option.value = document.id;
    return option;
  }));
  if (!documents.length) select.append(el("option", { text: "Upload a PDF first" }));
}

// ---------------------------------------------------------------------------
// Claims
// ---------------------------------------------------------------------------
const STATUS_LABELS = { supports: "Supported", contradicts: "Contradicted", qualifies: "Qualifies", unclear: "Unclear" };

async function loadClaims() {
  const claims = await request(`/api/projects/${state.activeProject.id}/claims`);
  const list = $("#claim-list");
  if (!claims.length) {
    list.replaceChildren(emptyCard("No claims yet", "Add factual claims and link each to a source that supports, contradicts, or qualifies it."));
    return;
  }
  list.replaceChildren(...claims.map((claim) => {
    const card = el("article", { className: `claim-card claim-${claim.status}` });
    const heading = el("div", { className: "card-heading" });
    heading.append(el("span", { className: "claim-status", text: STATUS_LABELS[claim.status] || label(claim.status) }));
    const deleteBtn = el("button", { className: "icon-button", text: "×", title: "Delete claim" });
    deleteBtn.onclick = async () => {
      if (!confirm("Delete this claim and all its linked evidence?")) return;
      try {
        await request(`/api/projects/${state.activeProject.id}/claims/${claim.id}`, { method: "DELETE" });
        await loadClaims();
        toast("Claim removed.");
      } catch (error) { toast(error.message); }
    };
    heading.append(deleteBtn);
    card.append(heading, el("p", { className: "claim-text", text: claim.text }));
    if (claim.evidence && claim.evidence.length) {
      const evidenceList = el("ul", { className: "claim-evidence-list" });
      claim.evidence.forEach((ev) => {
        const item = el("li", { className: "claim-evidence-item" });
        item.append(el("span", { className: `evidence-rel evidence-${ev.relationship}`, text: STATUS_LABELS[ev.relationship] }));
        if (ev.excerpt) item.append(el("span", { className: "evidence-excerpt-inline", text: ` "${ev.excerpt}"` }));
        evidenceList.append(item);
      });
      card.append(evidenceList);
    }
    return card;
  }));
}

// ---------------------------------------------------------------------------
// Research runs
// ---------------------------------------------------------------------------
function renderResearchRun(run) {
  const card = el("article", { className: `run-card status-${run.status}` });
  const header = el("div", { className: "run-heading" });
  header.append(el("span", { className: "run-status", text: label(run.status) }), el("time", { text: formatDate(run.created_at) }));
  // Retry button for failed/needs_configuration runs with attempts left
  if ((run.status === "failed" || run.status === "needs_configuration") && run.attempt_count < 3) {
    const retryBtn = el("button", { className: "quiet-button retry-button", text: "Retry" });
    retryBtn.onclick = async () => {
      retryBtn.disabled = true;
      try {
        await request(`/api/projects/${state.activeProject.id}/research-runs/${run.id}/retry`, { method: "POST" });
        await loadResearchRuns(true);
        toast("Run requeued.");
      } catch (error) { toast(error.message); retryBtn.disabled = false; }
    };
    header.append(retryBtn);
  }
  card.append(header, el("h3", { text: run.question }));
  if (Array.isArray(run.research_plan) && run.research_plan.length) {
    const plan = el("ol", { className: "research-plan" });
    run.research_plan.forEach((step) => plan.append(el("li", { text: step })));
    card.append(plan);
  }
  if (run.answer) card.append(el("p", { className: "run-answer", text: run.answer }));
  if (run.error_message) card.append(el("p", { className: "run-error", text: run.error_message }));
  return card;
}

async function loadResearchRuns(poll = true) {
  if (!state.activeProject) return;
  const projectId = state.activeProject.id;
  const runs = await request(`/api/projects/${projectId}/research-runs`);
  if (!state.activeProject || state.activeProject.id !== projectId) return;
  const list = $("#research-runs");
  if (!runs.length) {
    list.replaceChildren(emptyCard("No research runs yet", "Ask a focused question after saving evidence notes. The run will retain the source snapshot used for its response."));
    return;
  }
  list.replaceChildren(...runs.map(renderResearchRun));
  if (poll && runs.some((run) => run.status === "queued" || run.status === "running")) {
    window.clearTimeout(state.pollTimer);
    state.pollTimer = window.setTimeout(() => loadResearchRuns(true).catch((error) => toast(error.message)), 2500);
  }
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------
async function authenticate() {
  try { state.user = await request("/api/auth/me"); } catch { state.user = null; }
  if (state.user) {
    $("#auth-screen").hidden = true;
    $("#workspace").hidden = false;
    $("#account").hidden = false;
    $("#account-name").textContent = state.user.display_name || state.user.email;
    await loadProjects();
    return;
  }
  const methods = await request("/api/auth/methods");
  $("#auth-screen").hidden = false;
  $("#workspace").hidden = true;
  $("#google-login").hidden = !methods.google_configured;
  $("#password-login").hidden = !methods.password_login;
  $("#auth-note").textContent = methods.google_configured
    ? "Sign in with your local account or Google."
    : "Create a local account to access your private research workspace.";
}

// ---------------------------------------------------------------------------
// Event listeners
// ---------------------------------------------------------------------------
$("#password-login").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  try {
    await request("/api/auth/login", { method: "POST", body: JSON.stringify(Object.fromEntries(form)) });
    await authenticate();
  } catch (error) { toast(error.message); }
});

$("#show-register").onclick = () => { $("#register-form").hidden = !$("#register-form").hidden; };

$("#register-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  try {
    await request("/api/auth/register", { method: "POST", body: JSON.stringify(Object.fromEntries(form)) });
    toast("Account created.");
    await authenticate();
  } catch (error) { toast(error.message); }
});

$("#logout").onclick = async () => {
  await request("/api/auth/logout", { method: "POST" });
  state.user = null;
  showEmpty();
  $("#account").hidden = true;
  authenticate();
};

$("#new-project").onclick = () => $("#project-dialog").showModal();
$("#empty-new-project").onclick = () => $("#project-dialog").showModal();
$("#add-context").onclick = () => $("#context-dialog").showModal();
$("#add-claim").onclick = () => $("#claim-dialog").showModal();
$("#refresh-runs").onclick = () => loadResearchRuns(true).catch((error) => toast(error.message));
document.querySelectorAll("[data-close]").forEach((button) => { button.onclick = () => button.closest("dialog").close(); });

$("#delete-project").onclick = async () => {
  if (!state.activeProject) return;
  if (!confirm(`Permanently delete "${state.activeProject.title}" and all its data?`)) return;
  try {
    await request(`/api/projects/${state.activeProject.id}`, { method: "DELETE" });
    state.activeProject = null;
    toast("Project deleted.");
    await loadProjects();
  } catch (error) { toast(error.message); }
};

$("#project-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(event.currentTarget));
  try {
    const project = await request("/api/projects", { method: "POST", body: JSON.stringify(data) });
    event.currentTarget.reset();
    $("#project-dialog").close();
    state.activeProject = null;
    await loadProjects();
    await selectProject(project.id);
    toast("Project created.");
  } catch (error) { toast(error.message); }
});

$("#context-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(event.currentTarget));
  if (!data.rationale) delete data.rationale;
  try {
    await request(`/api/projects/${state.activeProject.id}/context`, { method: "POST", body: JSON.stringify(data) });
    event.currentTarget.reset();
    $("#context-dialog").close();
    await loadContext();
    toast("Research context saved.");
  } catch (error) { toast(error.message); }
});

$("#source-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(event.currentTarget));
  ["url", "doi", "year", "evidence_excerpt", "locator", "authors"].forEach((key) => { if (!data[key]) delete data[key]; });
  if (data.authors) data.authors = data.authors.split(";").map((author) => author.trim()).filter(Boolean);
  if (data.year) data.year = Number(data.year);
  try {
    await request(`/api/projects/${state.activeProject.id}/sources`, { method: "POST", body: JSON.stringify(data) });
    event.currentTarget.reset();
    await loadSources();
    toast("Evidence source saved.");
  } catch (error) { toast(error.message); }
});

$("#source-search-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = new FormData(event.currentTarget).get("q").toString().trim();
  try {
    const results = await request(`/api/projects/${state.activeProject.id}/sources/search?q=${encodeURIComponent(query)}`);
    renderSourceResults(results);
  } catch (error) { toast(error.message); }
});

$("#dataset-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = new FormData(event.currentTarget).get("file");
  if (!file || !file.name) return;
  try {
    const response = await fetch(`/api/projects/${state.activeProject.id}/analysis/datasets`, { method: "POST", body: new FormData(event.currentTarget) });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || "Dataset upload failed.");
    event.currentTarget.reset();
    await loadDatasets();
    toast("Dataset uploaded.");
  } catch (error) { toast(error.message); }
});

$("#analysis-submit").addEventListener("click", async () => {
  const datasetId = $("#dataset-select").value;
  const prompt = $("#analysis-prompt").value.trim();
  if (!datasetId || !prompt) return toast("Choose a dataset and enter an analysis prompt.");
  const button = $("#analysis-submit");
  button.disabled = true;
  try {
    const response = await request(`/api/projects/${state.activeProject.id}/analysis/runs`, { method: "POST", body: JSON.stringify({ dataset_id: datasetId, prompt }) });
    $("#analysis-result").textContent = JSON.stringify(response.result, null, 2);
    toast("Analysis completed.");
  } catch (error) { toast(error.message); }
  finally { button.disabled = false; }
});

$("#literature-upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = new FormData(event.currentTarget).get("file");
  if (!file || !file.name) return;
  try {
    const response = await fetch(`/api/projects/${state.activeProject.id}/literature-review/documents`, {
      method: "POST",
      body: new FormData(event.currentTarget),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || "PDF upload failed.");
    event.currentTarget.reset();
    await loadLiteratureDocuments();
    toast("PDF uploaded and text extracted.");
  } catch (error) { toast(error.message); }
});

$("#literature-submit").addEventListener("click", async () => {
  const documentId = $("#literature-document").value;
  const question = $("#literature-question").value.trim();
  const citationStyle = $("#literature-style").value;
  if (!documentId || !question) return toast("Upload a PDF and enter a research question.");
  if (!citationStyle) return toast("Choose APA or MLA before generating the review.");
  const button = $("#literature-submit");
  button.disabled = true;
  try {
    const result = await request(`/api/projects/${state.activeProject.id}/literature-review/generate`, {
      method: "POST",
      body: JSON.stringify({ document_id: documentId, research_question: question, citation_style: citationStyle }),
    });
    $("#literature-result").textContent = result.review;
    toast(`${citationStyle} literature review generated.`);
  } catch (error) { toast(error.message); }
  finally { button.disabled = false; }
});

$("#claim-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(event.currentTarget));
  try {
    await request(`/api/projects/${state.activeProject.id}/claims`, { method: "POST", body: JSON.stringify(data) });
    event.currentTarget.reset();
    $("#claim-dialog").close();
    await loadClaims();
    toast("Claim saved.");
  } catch (error) { toast(error.message); }
});

$("#research-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("#research-submit");
  const question = $("#research-question").value.trim();
  button.disabled = true;
  button.textContent = "Starting…";
  try {
    await request(`/api/projects/${state.activeProject.id}/research-runs`, { method: "POST", body: JSON.stringify({ question }) });
    $("#research-question").value = "";
    await loadResearchRuns(true);
    toast("Research run started.");
  } catch (error) { toast(error.message); }
  finally { button.disabled = false; button.textContent = "Run research"; }
});

authenticate().catch((error) => toast(error.message));
