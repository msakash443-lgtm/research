import { $, el, label, toast, confirmAction } from "../dom.js";
import { request, projectApi } from "../api.js";
import { GATE_NAMES } from "../workflow.js";

// One gate's row, reused on Scope (G1), Search (G2) and the full Approvals list.
export function gateCard(ctx, code) {
  const gate = (ctx.summary.gates || []).find((g) => g.code === code);
  const list = el("ul", { className: "gate-list" });
  if (!gate) return list;
  const row = el("li", { className: `gate-row gate-${gate.status}` });
  row.append(
    el("span", { className: "gate-code", text: gate.code }),
    el("span", { className: "gate-title", text: GATE_NAMES[gate.code] }),
    el("span", { className: "gate-status", text: label(gate.status) })
  );
  if (gate.note) row.append(el("p", { className: "small muted gate-note", text: `Note: ${gate.note}` }));
  if (gate.status !== "approved" && (gate.waiting_for || []).length > 0) {
    row.append(el("p", { className: "small muted gate-waiting", text: `Waits for ${gate.waiting_for.join(", ")}` }));
  }
  if (gate.can_reopen) {
    const reopen = el("button", { className: "quiet-button", text: "Reopen" });
    reopen.addEventListener("click", () => {
      const dialog = $("#reopen-dialog");
      dialog.dataset.gateCode = gate.code;
      $("#reopen-gate-label").textContent = `${gate.code} · ${GATE_NAMES[gate.code]}`;
      dialog.showModal();
    });
    row.append(reopen);
  }
  if (gate.can_decide && gate.status !== "approved") {
    const approve = el("button", { text: "Approve" });
    approve.addEventListener("click", async () => {
      const ok = await confirmAction(
        `Approve ${gate.code} (${GATE_NAMES[gate.code]})?`,
        "This is your decision as a person. Work waiting on this gate will start.",
        "Approve"
      );
      if (!ok) return;
      approve.disabled = true;
      try {
        await request(projectApi(ctx.project.id, `/gates/${gate.code}/approve`), { method: "POST", body: "{}" });
        await ctx.refresh();
      } catch (error) {
        toast(error.message);
      } finally {
        approve.disabled = false;
      }
    });
    const reject = el("button", { className: "quiet-button", text: "Reject" });
    reject.addEventListener("click", () => {
      const dialog = $("#reject-dialog");
      dialog.dataset.gateCode = gate.code;
      $("#reject-gate-label").textContent = `${gate.code} · ${GATE_NAMES[gate.code]}`;
      dialog.showModal();
    });
    row.append(approve, reject);
  }
  list.append(row);
  return list;
}

// One "Next: <stage>" card from GET .../stage's `next` array.
export function advanceCard(ctx, option) {
  const box = el("div", { className: "next-option" });
  box.append(el("p", { text: `Next: ${label(option.stage)}` }));
  if (option.ready) {
    if (ctx.canWrite) {
      const go = el("button", { text: `Move to ${label(option.stage)}` });
      go.addEventListener("click", async () => {
        go.disabled = true;
        try {
          await request(projectApi(ctx.project.id, "/stage/advance"), {
            method: "POST",
            body: JSON.stringify({ stage: option.stage }),
          });
          await ctx.refresh();
        } catch (error) {
          toast(error.message);
        } finally {
          go.disabled = false;
        }
      });
      box.append(go);
    } else {
      box.append(el("p", { className: "muted small", text: "Ready to move forward; an owner or co-author can do this." }));
    }
  } else {
    const gate = option.gate && (ctx.summary.gates || []).find((g) => g.code === option.gate);
    const roles = gate?.required_roles?.map(label).join(" or ");
    box.append(
      el("p", {
        className: "muted small",
        text: `Needs gate ${option.gate} (${GATE_NAMES[option.gate]}) approved by a person${
          roles ? `: ${roles}` : ""
        }. Status: ${label(option.gate_status)}.`,
      })
    );
  }
  return box;
}

export function errorCard(message, retry) {
  const card = el("div", { className: "empty-card" });
  card.append(el("h3", { text: "Couldn't load this" }), el("p", { text: message }));
  const button = el("button", { className: "quiet-button", text: "Retry" });
  button.addEventListener("click", retry);
  card.append(button);
  return card;
}
