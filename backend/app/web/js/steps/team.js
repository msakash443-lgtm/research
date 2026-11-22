import { $, el, label, toast, confirmAction, saveOnce } from "../dom.js";
import { request, projectApi } from "../api.js";
import { CREDIT_ROLES, assignmentsAfter, creditSelection, levelOf, matrixRows } from "../teamLogic.js";

const ROLE_DESCRIPTION = {
  owner: "Owner edits, approves and manages members.",
  co_author: "Co-author edits.",
  supervisor: "Supervisor approves gates.",
  reviewer: "Reviewer reads.",
};

function openInviteDialog(ctx) {
  const dialog = $("#invite-dialog");
  const form = $("#invite-form");
  form.reset();
  form.onsubmit = (event) => {
    event.preventDefault();
    saveOnce(form, async () => {
      const data = Object.fromEntries(new FormData(form));
      try {
        await request(projectApi(ctx.project.id, "/members"), { method: "POST", body: JSON.stringify(data) });
        dialog.close();
        toast("Member invited.");
        await ctx.refresh();
      } catch (error) {
        toast(error.message);
      }
    });
  };
  dialog.showModal();
}

function memberName(ctx, userId) {
  const member = (ctx.summary.members || []).find((m) => m.user_id === userId);
  return member ? member.display_name || member.email : userId;
}

// CRediT contributions (X.226): who contributed what, for the manuscript's author-contributions statement.
// Reporting only: it grants no access and is not the responsibility matrix.
async function creditPanel(ctx) {
  const panel = el("div", { className: "panel" });
  panel.append(el("h2", { text: "Contributions (CRediT)" }),
    el("p", { className: "muted small", text: "The 14 CRediT roles name what each person contributed to the manuscript. This is reporting for the author-contributions statement; it does not change what anyone may do in this project." }));
  let people;
  try {
    people = await request(projectApi(ctx.project.id, "/manuscript/credit"));
  } catch (error) {
    panel.append(el("p", { className: "muted small", text: error.message }));
    return panel;
  }
  for (const person of people) {
    const details = el("details", { className: "credit-member" });
    details.append(el("summary", { text: `${person.name}: ${person.roles.length ? person.roles.join(", ") : "no contribution recorded"}` }));
    let chosen = person.roles;
    const box = el("fieldset", { className: "strategy-checklist" });
    box.disabled = !ctx.canWrite;
    CREDIT_ROLES.forEach((role) => {
      const checkbox = el("input", { type: "checkbox", attrs: { value: role } });
      checkbox.checked = chosen.includes(role);
      checkbox.addEventListener("change", () => { chosen = creditSelection(chosen, role, checkbox.checked); });
      box.append(el("label", { className: "checkbox-label", children: [checkbox, el("span", { text: role })] }));
    });
    details.append(box);
    if (ctx.canWrite) {
      const save = el("button", { className: "quiet-button", text: "Save contributions" });
      save.addEventListener("click", async () => {
        save.disabled = true;
        try {
          await request(projectApi(ctx.project.id, `/manuscript/credit/${person.member_id}`), { method: "PUT", body: JSON.stringify({ roles: chosen }) });
          toast("Contributions saved.");
          await ctx.refresh();
        } catch (error) {
          toast(error.message);
          save.disabled = false;
        }
      });
      details.append(save);
    }
    panel.append(details);
  }
  return panel;
}

// The responsibility matrix (X.226): who leads or supports each area of day-to-day work. Informational and apart
// from both the project role (authorisation) and CRediT (reporting); only the owner edits it.
async function responsibilityPanel(ctx) {
  const panel = el("div", { className: "panel" });
  panel.append(el("h2", { text: "Responsibilities" }),
    el("p", { className: "muted small", text: "Who leads or supports each area of the work now. One lead per area answers for it. This does not give or remove any access, and it is not the CRediT contribution list." }));
  let matrix;
  try {
    matrix = await request(projectApi(ctx.project.id, "/responsibilities"));
  } catch (error) {
    panel.append(el("p", { className: "muted small", text: error.message }));
    return panel;
  }
  const members = ctx.summary.members || [];
  const table = el("table", { className: "versions-table" });
  const head = el("tr", { children: [el("th", { text: "Area" }), ...members.map((m) => el("th", { text: m.display_name || m.email }))] });
  table.append(head);
  for (const row of matrixRows(matrix)) {
    const tr = el("tr", { children: [el("td", { text: row.label })] });
    for (const member of members) {
      const select = el("select", { attrs: { "aria-label": `${row.label}: ${memberName(ctx, member.user_id)}` } });
      [["", "—"], ["lead", "Lead"], ["support", "Support"]].forEach(([value, text]) => select.append(el("option", { text, attrs: { value } })));
      select.value = levelOf(row, member.user_id);
      select.disabled = !ctx.isOwner;
      select.addEventListener("change", async () => {
        select.disabled = true;
        try {
          await request(projectApi(ctx.project.id, `/responsibilities/${row.code}`), {
            method: "PUT", body: JSON.stringify({ assignments: assignmentsAfter(row, member.user_id, select.value) }),
          });
          toast("Responsibilities saved.");
          await ctx.refresh();
        } catch (error) {
          toast(error.message);
          select.disabled = false;
        }
      });
      tr.append(el("td", { children: [select] }));
    }
    table.append(tr);
  }
  panel.append(table);
  return panel;
}

async function render(root, ctx) {
  const panel = el("div", { className: "panel" });
  const title = el("div", { className: "section-title" });
  title.append(el("div", { children: [el("h2", { text: "Team" })] }));
  if (ctx.isOwner) {
    const invite = el("button", { text: "Invite member" });
    invite.addEventListener("click", () => openInviteDialog(ctx));
    title.append(invite);
  }
  panel.append(title);

  const roleNotes = el("ul", { className: "role-notes" });
  Object.entries(ROLE_DESCRIPTION).forEach(([role, text]) => roleNotes.append(el("li", { text: `${label(role)}: ${text}` })));
  panel.append(roleNotes);

  const list = el("div", { className: "source-list" });
  (ctx.summary.members || []).forEach((member) => {
    const card = el("article", { className: "source-card" });
    const you = member.user_id === ctx.user.id ? " (you)" : "";
    card.append(el("h3", { text: `${member.display_name || member.email}${you}` }));
    card.append(el("p", { className: "source-locator", text: `${member.email} · ${label(member.role)}` }));
    if (ctx.isOwner) {
      const remove = el("button", { className: "quiet-button", text: "Remove" });
      remove.addEventListener("click", async () => {
        const ok = await confirmAction("Remove member?", `${member.display_name || member.email} will lose access to this project.`, "Remove");
        if (!ok) return;
        try {
          await request(projectApi(ctx.project.id, `/members/${member.user_id}`), { method: "DELETE" });
          toast("Member removed.");
          await ctx.refresh();
        } catch (error) {
          toast(error.message);
        }
      });
      card.append(remove);
    }
    list.append(card);
  });
  panel.append(list);

  root.replaceChildren(panel, await creditPanel(ctx), await responsibilityPanel(ctx));
}

export default { render };
