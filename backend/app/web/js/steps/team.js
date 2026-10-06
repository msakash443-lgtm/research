import { $, el, label, toast, confirmAction, saveOnce } from "../dom.js";
import { request, projectApi } from "../api.js";

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

function render(root, ctx) {
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

  root.replaceChildren(panel);
}

export default { render };
