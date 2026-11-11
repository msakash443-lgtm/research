import { $, el, label, toast, emptyCard } from "../dom.js";
import { request, projectApi } from "../api.js";
import { gateCard } from "./common.js";
import { renderFrame } from "./frame.js";
import { canChangeResearchType, canSaveResearchType, chainRows, researchTypeLockReason, selectedResearchType } from "../researchTypeLogic.js";
import { FEASIBILITY_FIELDS, FEASIBILITY_STATUSES, feasibilityByItem, ideaBriefPayload, landscapeScanPath, splitScopeContext } from "../strategyLogic.js";

const KIND_ORDER = ["question", "objective", "hypothesis", "methodology", "variable", "decision"];

function contextEditor(item, ctx) {
  const form = el("form", { className: "context-edit" });
  const statement = el("textarea", { attrs: { name: "content", required: "", "aria-label": "Statement" } });
  statement.value = item.content;
  const reason = el("textarea", { attrs: { name: "rationale", "aria-label": "Why does this matter?", placeholder: "Why does this matter? (optional)" } });
  reason.value = item.rationale || "";
  const save = el("button", { type: "submit", text: "Save changes" });
  const cancel = el("button", { type: "button", className: "quiet-button", text: "Cancel" });
  cancel.addEventListener("click", () => ctx.refresh());
  form.append(statement, reason, el("div", { className: "dialog-actions", children: [cancel, save] }));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    save.disabled = true;
    const body = { content: statement.value.trim() };
    if (reason.value.trim() !== (item.rationale || "")) body.rationale = reason.value.trim() || null;
    try {
      await request(projectApi(ctx.project.id, `/context/${item.id}`), { method: "PATCH", body: JSON.stringify(body) });
      toast("Entry updated. The earlier wording is kept in its history.");
      await ctx.refresh();
    } catch (error) {
      save.disabled = false;
      toast(error.message);
    }
  });
  return form;
}

function contextActions(card, item, ctx) {
  const edit = el("button", { type: "button", className: "quiet-button", text: "Edit" });
  edit.addEventListener("click", () => {
    card.replaceChildren(contextEditor(item, ctx));
    card.querySelector("textarea").focus();
  });
  const retire = el("button", { type: "button", className: "quiet-button", text: "Retire" });
  retire.addEventListener("click", async () => {
    if (!window.confirm("Retire this entry? It is hidden from the project and from the AI, but kept in the audit record.")) return;
    try {
      await request(projectApi(ctx.project.id, `/context/${item.id}/retire`), { method: "POST" });
      toast("Entry retired.");
      await ctx.refresh();
    } catch (error) {
      toast(error.message);
    }
  });
  const remove = el("button", { type: "button", className: "quiet-button danger", text: "Delete" });
  remove.addEventListener("click", async () => {
    if (!window.confirm("Delete this entry permanently? This cannot be undone. To keep a record of it, use Retire instead.")) return;
    try {
      await request(projectApi(ctx.project.id, `/context/${item.id}`), { method: "DELETE" });
      toast("Entry deleted.");
      await ctx.refresh();
    } catch (error) {
      toast(error.message);
    }
  });
  return el("div", { className: "context-actions", children: [edit, retire, remove] });
}

function contextCard(item, ctx) {
  const leftOut = item.used_by_agent === false;
  const card = el("article", { className: leftOut ? "context-card not-sent" : "context-card" });
  card.append(
    el("span", {
      className: "context-kind",
      text: leftOut ? `${label(item.kind)} · Not sent to the agent` : label(item.kind),
    }),
    el("h3", { text: item.content })
  );
  card.append(el("p", { text: item.rationale ? `Reason: ${item.rationale}` : "Saved to this project's structured research memory." }));
  if (item.history?.length) card.append(el("p", { className: "muted small", text: `Edited ${item.history.length} time${item.history.length === 1 ? "" : "s"}; earlier wording is kept.` }));
  if (ctx.canWrite) card.append(contextActions(card, item, ctx));
  return card;
}

function openAddContext(kind = null) {
  const form = $("#context-form");
  form.reset();
  if (kind) form.elements.kind.value = kind;
  $("#context-dialog").showModal();
}
function field(labelText, control) {
  const wrapper = el("label", { className: "strategy-field" });
  wrapper.append(el("span", { text: labelText }), control);
  return wrapper;
}

function readinessNotice(brief) {
  const state = brief.ready_for_g1 ? "Checklist complete; G1 is ready for a person's decision." : "G1 remains a human approval; readiness does not approve it.";
  const notice = el("p", { className: "muted small", text: `${state} ${brief.note || ""}` });
  if (brief.open_items?.length) notice.append(el("span", { text: ` Open: ${brief.open_items.map(label).join(", ")}.` }));
  if (brief.blockers?.length) notice.append(el("span", { className: "strategy-blockers", text: ` Blockers: ${brief.blockers.map(label).join(", ")}.` }));
  return notice;
}

function readOnlyBrief(box, brief) {
  if (!brief.exists) {
    box.append(emptyCard("No idea brief saved", "A project owner or co-author can record the problem, why it matters, and feasibility."));
    return;
  }
  box.append(
    el("p", { text: `Problem: ${brief.problem || "Not written yet"}` }),
    el("p", { text: `Why it matters: ${brief.why_it_matters || "Not written yet"}` }),
    el("p", { text: `Risks: ${brief.risks || "Not recorded"}` }),
    readinessNotice(brief)
  );
  const selected = brief.ideas?.find((idea) => idea.id === brief.selected_idea_id);
  box.append(el("p", { text: `Selected idea: ${selected?.content || "None"}` }));
  const answers = feasibilityByItem(brief.feasibility);
  const checklist = el("div", { className: "strategy-feasibility-readonly" });
  checklist.append(el("h3", { text: "Feasibility" }));
  FEASIBILITY_FIELDS.forEach(({ item, label: itemLabel }) => {
    const answer = answers[item] || {};
    const status = answer.status ? label(answer.status) : "Not assessed";
    checklist.append(el("p", { text: `${itemLabel}: ${status}${answer.note ? ` — ${answer.note}` : ""}` }));
  });
  box.append(checklist);
}

async function ideaBriefPanel(ctx) {
  const box = el("div", { className: "panel idea-brief-panel" });
  box.append(el("h2", { text: "Idea brief" }));
  let brief;
  try {
    brief = await request(projectApi(ctx.project.id, "/idea-brief"));
  } catch (error) {
    box.append(el("p", { className: "strategy-error", text: error.message, attrs: { role: "alert" } }));
    return box;
  }
  if (!ctx.canWrite) {
    readOnlyBrief(box, brief);
    return box;
  }

  const form = el("form", { className: "strategy-form idea-brief-form" });
  const problem = el("textarea", { attrs: { name: "problem", required: "", maxlength: "5000", "aria-label": "What problem will this research address?" } });
  const why = el("textarea", { attrs: { name: "why_it_matters", required: "", maxlength: "5000", "aria-label": "Why does the problem matter?" } });
  const risks = el("textarea", { attrs: { name: "risks", maxlength: "5000", "aria-label": "Risks and constraints" } });
  problem.value = brief.problem || "";
  why.value = brief.why_it_matters || "";
  risks.value = brief.risks || "";
  form.append(field("Problem", problem), field("Why it matters", why), field("Risks and constraints", risks));

  const idea = el("select", { attrs: { name: "selected_idea_id", "aria-label": "Selected idea" } });
  idea.append(el("option", { text: "Choose an idea (optional)", attrs: { value: "" } }));
  (brief.ideas || []).forEach((entry) => {
    const option = el("option", { text: entry.content, attrs: { value: entry.id } });
    option.selected = entry.id === brief.selected_idea_id;
    idea.append(option);
  });
  form.append(field("Selected idea", idea));

  const currentFeasibility = feasibilityByItem(brief.feasibility);
  const feasibility = {};
  const checklist = el("fieldset", { className: "strategy-feasibility" });
  checklist.append(el("legend", { text: "Feasibility — your assessment" }));
  FEASIBILITY_FIELDS.forEach(({ item, label: itemLabel }) => {
    const answer = currentFeasibility[item] || {};
    const status = el("select", { attrs: { name: `feasibility_${item}`, "aria-label": `${itemLabel} status` } });
    FEASIBILITY_STATUSES.forEach((value) => {
      const option = el("option", { text: label(value), attrs: { value } });
      option.selected = (answer.status || "unsure") === value;
      status.append(option);
    });
    const note = el("input", { type: "text", attrs: { name: `feasibility_note_${item}`, maxlength: "2000", placeholder: "Optional note", "aria-label": `${itemLabel} note` } });
    note.value = answer.note || "";
    feasibility[item] = { status, note };
    checklist.append(field(itemLabel, status), field(`${itemLabel} note`, note));
  });
  form.append(checklist);

  const save = el("button", { type: "submit", text: "Save idea brief" });
  form.append(el("div", { className: "form-actions", children: [save] }));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    save.disabled = true;
    try {
      const answers = Object.fromEntries(FEASIBILITY_FIELDS.map(({ item }) => [
        item, { status: feasibility[item].status.value, note: feasibility[item].note.value },
      ]));
      await request(projectApi(ctx.project.id, "/idea-brief"), {
        method: "PUT",
        body: JSON.stringify(ideaBriefPayload({
          problem: problem.value, whyItMatters: why.value, risks: risks.value,
          selectedIdeaId: idea.value, feasibility: answers,
        })),
      });
      toast("Idea brief saved.");
      await ctx.refresh();
    } catch (error) {
      toast(error.message);
    } finally {
      save.disabled = false;
    }
  });
  box.append(form, readinessNotice(brief));
  return box;
}

function landscapeResults(scan) {
  const results = el("div", { className: "landscape-scan-results" });
  results.append(el("p", { className: "muted small", text: scan.note }));
  const services = scan.services || [];
  const serviceSummary = services.length
    ? services.map((s) => `${label(s.service)}: ${s.status === "ok" ? `${s.count} found` : `failed${s.error ? ` (${s.error})` : ""}`}`).join(" · ")
    : "Not searched";
  results.append(el("p", { text: `${serviceSummary} · ${scan.sample_size} unique records sampled.` }));
  if (!scan.sample_size) {
    results.append(emptyCard("No records in this scan", "Try a broader or more specific query. This scan does not establish that no literature exists."));
    return results;
  }
  const list = (title, entries) => {
    const section = el("section", { className: "landscape-scan-section" });
    section.append(el("h3", { text: title }));
    if (!entries.length) section.append(el("p", { className: "muted", text: "No records in this sample." }));
    else {
      const ul = el("ul");
      entries.forEach((entry) => ul.append(el("li", {
        text: `${entry.title}${entry.year ? ` (${entry.year})` : ""}${entry.cited_by_count == null ? "" : ` · ${entry.cited_by_count} citations`}`,
      })));
      section.append(ul);
    }
    return section;
  };
  results.append(
    list("Most cited in the sample", scan.top_cited_in_sample || []),
    list("Recent reviews in the sample", scan.recent_reviews || [])
  );
  const years = Object.entries(scan.volume_by_year_in_sample || {}).sort(([a], [b]) => Number(a) - Number(b));
  if (years.length) results.append(el("p", { text: `Sample counts by year: ${years.map(([year, count]) => `${year}: ${count}`).join(", ")}` }));
  return results;
}

function landscapeScanPanel(ctx) {
  const box = el("div", { className: "panel landscape-scan-panel" });
  box.append(
    el("h2", { text: "Landscape scan" }),
    el("p", { className: "muted", text: "A quick, non-exhaustive snapshot from every enabled scholarly service. It is not a systematic search." })
  );
  const form = el("form", { className: "strategy-form landscape-scan-form" });
  const query = el("input", { type: "search", attrs: { name: "query", required: "", maxlength: "300", placeholder: "Topic or key phrase", "aria-label": "Landscape scan query" } });
  const scanButton = el("button", { type: "submit", text: "Run landscape scan" });
  const output = el("div", { className: "landscape-scan-output", attrs: { "aria-live": "polite" } });
  form.append(field("Search phrase", query), scanButton);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    scanButton.disabled = true;
    output.replaceChildren(el("p", { className: "muted", text: "Running scan…" }));
    try {
      const scan = await request(landscapeScanPath(ctx.project.id, query.value));
      output.replaceChildren(landscapeResults(scan));
    } catch (error) {
      output.replaceChildren(el("p", { className: "strategy-error", text: error.message, attrs: { role: "alert" } }));
    } finally {
      scanButton.disabled = false;
    }
  });
  box.append(form, output);
  return box;
}

function objectivesPanel(objectives, ctx) {
  const box = el("div", { className: "panel objectives-panel" });
  const heading = el("div", { className: "section-title" });
  heading.append(el("h2", { text: "Objectives" }));
  if (ctx.canWrite) {
    const add = el("button", { type: "button", text: "Add objective" });
    add.addEventListener("click", () => openAddContext("objective"));
    heading.append(add);
  }
  box.append(heading);
  if (!objectives.length) box.append(emptyCard("No objectives recorded", "Write the outcomes this project intends to achieve."));
  else {
    const list = el("div", { className: "context-list" });
    objectives.forEach((item) => list.append(contextCard(item, ctx)));
    box.append(list);
  }
  return box;
}

async function discipline(root, ctx) {
  const box = el("div", { className: "panel" });
  box.append(el("h2", { text: "Discipline profile" }));
  try {
    if (!ctx.cache.profiles) {
      ctx.cache.profiles = await request("/api/discipline-profiles");
    }
    const current = await request(projectApi(ctx.project.id, "/profile"));
    if (ctx.isOwner) {
      const select = el("select", { attrs: { name: "discipline" } });
      select.append(el("option", { text: "None", attrs: { value: "" } }));
      ctx.cache.profiles.forEach((profile) => {
        const option = el("option", { text: profile.label, attrs: { value: profile.name } });
        if (current.discipline === profile.name) option.selected = true;
        select.append(option);
      });
      const save = el("button", { text: "Save" });
      save.addEventListener("click", async () => {
        save.disabled = true;
        try {
          await request(projectApi(ctx.project.id, "/profile"), {
            method: "PUT",
            body: JSON.stringify({ discipline: select.value || null }),
          });
          toast("Discipline profile saved.");
          await ctx.refresh();
        } catch (error) {
          toast(error.message);
        } finally {
          save.disabled = false;
        }
      });
      const row = el("div", { className: "form-actions" });
      row.append(select, save);
      box.append(row);
    }
    const dl = el("dl", { className: "effective-settings" });
    const entries = [
      ["Citation style", current.effective.citation_style],
      ["Databases", current.effective.databases?.join(", ")],
      ["Methods", current.effective.methods?.join(", ")],
      ["Reporting guideline", current.effective.reporting_guideline],
    ];
    entries.forEach(([term, value]) => {
      dl.append(el("dt", { text: term }), el("dd", { text: value || "Not set" }));
    });
    box.append(dl);
  } catch (error) {
    box.append(el("p", { className: "muted small", text: error.message }));
  }
  return box;
}

// Research type picker (X.43.9): which stages this project goes through. The owner can change it only at idea
// with no approved gate; everyone else sees the saved type and its stage list. The server re-checks both rules.
async function researchTypePanel(ctx) {
  const box = el("div", { className: "panel" });
  box.append(el("h2", { text: "Research type" }));
  try {
    if (!ctx.cache.researchTypes) {
      ctx.cache.researchTypes = await request("/api/research-types");
    }
    const types = ctx.cache.researchTypes;
    const stage = ctx.summary.stage || {};
    const saved = stage.research_type;
    const changeable = canChangeResearchType({ isOwner: ctx.isOwner, stage: ctx.summary.stage?.stage, gates: ctx.summary.gates || [] });
    const lockReason = researchTypeLockReason({ isOwner: ctx.isOwner, stage: ctx.summary.stage?.stage, gates: ctx.summary.gates || [] });
    box.append(el("p", { className: "muted small", text: changeable
      ? "Choose the type before any gate is approved. It decides which stages this project passes through."
      : lockReason }));
    if (changeable) {
      const picker = el("div", { className: "type-options" });
      const state = { picked: saved };
      const save = el("button", { text: "Save research type" });
      const saveNote = el("span", { className: "muted small" });
      const sync = () => {
        const canSave = canSaveResearchType({ saved, picked: state.picked, changeable });
        save.disabled = !canSave;
        saveNote.textContent = canSave ? "" : !state.picked ? "Pick a type to save." : "Already saved.";
      };
      types.forEach((type) => {
        const option = el("label", { className: "type-option" });
        const input = el("input", { attrs: { type: "radio", name: "research-type", value: type.name } });
        input.checked = type.name === selectedResearchType({ saved, picked: state.picked });
        input.addEventListener("change", () => {
          state.picked = type.name;
          sync();
        });
        option.append(input, el("strong", { text: ` ${type.label}` }), el("span", { className: "muted small", text: ` · ${type.stages.length} stages` }));
        picker.append(option);
      });
      save.addEventListener("click", async () => {
        save.disabled = true;
        try {
          await request(projectApi(ctx.project.id, "/research-type"), {
            method: "PUT",
            body: JSON.stringify({ name: state.picked }),
          });
          toast("Research type saved.");
          await ctx.refresh();
        } catch (error) {
          toast(error.message);
          sync();
        }
      });
      sync();
      const row = el("div", { className: "form-actions" });
      row.append(save, saveNote);
      box.append(picker, row);
    } else {
      // Locked (wireframe Q3): every type stays visible, greyed out, with the reason above.
      const picker = el("div", { className: "type-options" });
      types.forEach((type) => {
        const option = el("label", { className: "type-option is-disabled" });
        const input = el("input", { attrs: { type: "radio", name: "research-type", value: type.name } });
        input.checked = type.name === saved;
        input.disabled = true;
        option.append(input, el("strong", { text: ` ${type.label}` }), el("span", { className: "muted small", text: ` · ${type.stages.length} stages` }));
        picker.append(option);
      });
      box.append(picker);
    }
    const rows = chainRows(stage.chain);
    const list = el("ol", { className: "research-type-stages" });
    rows.forEach((row) => {
      const text = `${row.label || label(row.stage)}${row.gate ? ` · ${row.gate}` : ""}${row.mandatory ? " (required in every type)" : ""}`;
      list.append(el("li", { text }));
    });
    box.append(el("p", { className: "muted small", text: `Stages in this project (${rows.length}):` }), list);
  } catch (error) {
    box.append(el("p", { className: "muted small", text: error.message }));
  }
  return box;
}

async function render(root, ctx) {
  const { objectives, context: items } = splitScopeContext(ctx.summary.context || []);
  const sections = [
    await ideaBriefPanel(ctx),
    landscapeScanPanel(ctx),
    objectivesPanel(objectives, ctx),
  ];

  const contextPanel = el("div", { className: "panel context-panel" });
  const title = el("div", { className: "section-title" });
  title.append(el("div", { children: [el("h2", { text: "Structured context" })] }));
  if (ctx.canWrite) {
    const add = el("button", { text: "Add context" });
    add.addEventListener("click", () => openAddContext());
    title.append(add);
  }
  contextPanel.append(title);
  if (items.length === 0) {
    contextPanel.append(emptyCard("Capture your first research decision", "Add a question, hypothesis, or methodological choice to make this project easier to continue later."));
  } else {
    const list = el("div", { className: "context-list" });
    KIND_ORDER.filter((kind) => kind !== "objective").forEach((kind) => {
      items.filter((item) => item.kind === kind).forEach((item) => list.append(contextCard(item, ctx)));
    });
    contextPanel.append(list);
  }
  sections.push(contextPanel);

  sections.push(await discipline(root, ctx));
  sections.push(await researchTypePanel(ctx));

  const gatePanel = el("div", { className: "panel" });
  gatePanel.append(el("h2", { text: "Approval" }));
  gatePanel.append(gateCard(ctx, "G1"));
  sections.push(gatePanel);

  renderFrame(root, ctx, { stepId: "scope", work: sections, next: ["scoped"] });
}

export default { render };
