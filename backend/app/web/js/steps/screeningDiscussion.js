import { el, label, toast, formatDate } from "../dom.js";
import { request } from "../api.js";
import { discussionAccess, noteBody, rulingRequest, threadLines } from "./discussionLogic.js";
import { OTHER_EXCLUSION_REASON } from "./screenLogic.js";

// The discussion thread and the independent ruling form of one reviewer disagreement (X.227), from the extended
// wireframe docs/wireframes/screening-queue.html. `who`: { userId, canWrite, canRule, locked, criteria, names, api,
// stage, refresh }. Every note and ruling goes through the server, which holds the real rules (X.225).
export async function discussionBlock(conflict, who) {
  const box = el("div", { className: "screen-discussion" });
  let notes = [];
  try {
    notes = await request(who.api(`/discussion?source_id=${conflict.source_id}&stage=${who.stage}`));
  } catch (error) {
    box.append(el("p", { className: "muted small", text: `The discussion could not be loaded: ${error.message}` }));
    return box;
  }
  const ruled = Boolean(conflict.adjudication);
  const access = discussionAccess({
    userId: who.userId, decisions: conflict.decisions, notes, ruled, locked: who.locked, canWrite: who.canWrite, canRule: who.canRule,
  });

  box.append(el("h4", { text: "Discussion between the reviewers" }));
  const lines = threadLines(notes, who.names);
  if (!lines.length) box.append(el("p", { className: "muted small", text: "No discussion recorded yet." }));
  else {
    const thread = el("ol", { className: "screen-thread" });
    lines.forEach((line) => {
      const item = el("li", { className: "small" });
      item.append(el("strong", { text: line.who }), el("span", { className: "muted", text: ` · ${formatDate(line.when)}` }));
      if (!line.current) item.append(el("span", { className: "muted", text: " · written before a reviewer changed their answer" }));
      item.append(el("p", { text: line.body }));
      thread.append(item);
    });
    box.append(thread);
  }

  if (access.canNote) {
    const text = el("textarea", { attrs: { rows: "2", maxlength: "2000", "aria-label": "Add to the discussion" } });
    const add = el("button", { type: "button", className: "quiet-button", text: "Add to the discussion" });
    add.addEventListener("click", async () => {
      const note = noteBody(text.value);
      if (!note.ok) { toast(note.message); return; }
      add.disabled = true;
      try {
        await request(who.api("/discussion"), { method: "POST", body: JSON.stringify({ source_id: conflict.source_id, stage: who.stage, body: note.body }) });
        toast("Added. Notes can't be edited later; the audit file keeps them.");
        await who.refresh();
      } catch (error) {
        toast(error.message);
        add.disabled = false;
      }
    });
    box.append(text, add);
  } else if (!ruled && !who.locked && who.canRule === false) {
    box.append(el("p", { className: "muted small", text: "Only the two reviewers write in the discussion." }));
  }

  if (ruled) {
    const ruling = conflict.adjudication;
    const by = who.names.get(String(ruling.decider)) || ruling.decider;
    box.append(el("p", { className: "small", text: `Ruled by ${by}: ${label(ruling.decision)}${ruling.reason_code ? ` (${ruling.reason_code})` : ""}. The reviewers' own decisions above stay as they are.` }));
    if (ruling.note) box.append(el("p", { className: "small", text: `Rationale: ${ruling.note}` }));
  }
  if (access.ruling.state !== "hidden") {
    box.append(el("h4", { text: ruled ? "Rule again" : "Independent ruling" }));
    if (access.ruling.state !== "open") box.append(el("p", { className: "muted small", text: access.ruling.message }));
    else box.append(rulingForm(conflict, who));
  }
  return box;
}

function rulingForm(conflict, who) {
  const form = el("div", { className: "screen-ruling" });
  const decision = el("select", { attrs: { "aria-label": "Ruling" } });
  [["include", "Include"], ["maybe", "Maybe"], ["exclude", "Exclude"]].forEach(([value, text]) => decision.append(el("option", { text, attrs: { value } })));
  const code = el("select", { attrs: { "aria-label": "Reason code" } });
  const fill = () => {
    const kind = decision.value === "exclude" ? "exclude" : "include";
    code.replaceChildren(el("option", { text: decision.value === "exclude" ? "Choose the exclusion criterion" : "No criterion (optional)", attrs: { value: "" } }));
    who.criteria.filter((c) => c.kind === kind).forEach((c) => code.append(el("option", { text: `${c.code} ${c.text}`, attrs: { value: c.code } })));
    if (decision.value === "exclude") code.append(el("option", { text: "Any other (the rationale describes it)", attrs: { value: OTHER_EXCLUSION_REASON } }));
    code.hidden = decision.value === "maybe";
    if (decision.value === "maybe") code.value = "";
  };
  decision.addEventListener("change", fill);
  fill();
  const why = el("textarea", { attrs: { rows: "3", maxlength: "2000", "aria-label": "Why (required, in your own words)", placeholder: "Why (required, in your own words)" } });
  const save = el("button", { type: "button", text: "Record ruling" });
  save.addEventListener("click", async () => {
    const request_ = rulingRequest({ sourceId: conflict.source_id, stage: who.stage, decision: decision.value, reasonCode: code.value, rationale: why.value });
    if (!request_.ok) { toast(request_.message); return; }
    save.disabled = true;
    try {
      await request(who.api("/decisions/adjudicate"), { method: "POST", body: JSON.stringify(request_.body) });
      toast(`Ruling recorded: ${label(decision.value)}. Both reviewers' decisions and the discussion stay in the audit file.`);
      await who.refresh();
    } catch (error) {
      toast(error.message);
      save.disabled = false;
    }
  });
  form.append(decision, code, why, save);
  return form;
}
