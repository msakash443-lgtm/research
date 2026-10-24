// Account privacy screen (X.85): the privacy notice and processor list (X.4.9), a download of the
// signed-in user's data (X.4.8), sign out everywhere (X.4.3) and erase account (X.4.7.1). Account-level only;
// it changes no project record and approves no gate. CSP-safe: handlers via addEventListener only.
import { el, toast } from "./dom.js";
import { request } from "./api.js";
import { errorCard } from "./steps/common.js";
import { confirmEmailMatches, processorRows } from "./privacyLogic.js";

function section(title, description) {
  const node = el("section", { className: "panel privacy-card" });
  node.append(el("h2", { text: title }));
  if (description) node.append(el("p", { className: "muted", text: description }));
  return node;
}

function action(copy, control, className = "") {
  const row = el("div", { className: `privacy-action ${className}`.trim() });
  row.append(el("div", { className: "privacy-action-copy", children: copy }), control);
  return row;
}

function noticePanel() {
  const node = section("Privacy notice");
  const notice = el("div", { className: "privacy-notice" });
  const list = el("ul", { className: "privacy-processors" });
  const status = el("p", { className: "muted small", text: "Loading the processor list…" });
  node.append(notice, el("h3", { text: "Processors currently enabled" }), status, list);

  request("/api/privacy/processors")
    .then((body) => {
      notice.replaceChildren(...String(body.notice || "").split(/\n\s*\n/).map((text) => el("p", { text })));
      const rows = processorRows(body.processors);
      status.textContent = rows.length ? "" : "No outside services are switched on for this installation.";
      list.replaceChildren(
        ...rows.map((row) =>
          el("li", {
            className: "privacy-processor",
            children: [
              el("strong", { text: row.label }),
              el("small", { text: `Purpose: ${row.purpose} · Host: ${row.host}` }),
              el("small", { text: `Data sent: ${row.dataSent}` }),
            ],
          }),
        ),
      );
    })
    .catch((error) => {
      status.replaceChildren(errorCard(`Couldn't load the processor list. ${error.message}`));
    });
  return node;
}

export function renderPrivacy(root, { user, confirmNoPendingCaptures, onAccountEnded }) {
  root.replaceChildren();
  const header = el("div", { className: "privacy-heading" });
  header.append(
    el("p", { className: "eyebrow", text: "ACCOUNT" }),
    el("h1", { text: "Privacy and your data" }),
    el("p", { className: "muted", text: "Manage the data held for your account. Project permissions and research decisions remain in each project workspace." }),
  );
  const actions = section("Account actions", "These controls affect your account across every project. They do not approve gates or change project records.");
  const list = el("div", { className: "privacy-actions" });
  const message = el("div", { className: "privacy-message" });

  const showError = (error) => message.replaceChildren(errorCard(error.message));

  const download = el("a", {
    className: "quiet-button",
    href: "/api/auth/export-my-data",
    attrs: { download: "my-personal-data.json" },
    text: "Download JSON",
  });

  const signOut = el("button", { type: "button", className: "quiet-button", text: "Sign out everywhere" });
  signOut.addEventListener("click", async () => {
    if (!(await confirmNoPendingCaptures("Sign out"))) return;
    try {
      await request("/api/auth/logout-everywhere", { method: "POST" });
      await onAccountEnded("You have been signed out everywhere.");
    } catch (error) {
      showError(error);
    }
  });

  const eraseToggle = el("button", { type: "button", className: "danger-button", text: "Erase account" });
  const confirmation = el("div", { className: "privacy-confirmation" });
  confirmation.hidden = true;
  const emailInput = el("input", { attrs: { type: "email", autocomplete: "off", placeholder: user?.email || "you@example.test" } });
  const confirmErase = el("button", { type: "button", className: "danger-button", text: "Confirm permanent erasure" });
  const cancelErase = el("button", { type: "button", className: "quiet-button", text: "Cancel" });

  eraseToggle.addEventListener("click", () => {
    confirmation.hidden = !confirmation.hidden;
    if (!confirmation.hidden) emailInput.focus();
  });
  cancelErase.addEventListener("click", () => {
    confirmation.hidden = true;
    emailInput.value = "";
  });
  confirmErase.addEventListener("click", async () => {
    const typed = emailInput.value;
    if (!confirmEmailMatches(typed, user?.email)) {
      toast("Type your account email to confirm erasure.");
      return;
    }
    if (!(await confirmNoPendingCaptures("Erase account"))) return;
    confirmErase.disabled = true;
    try {
      await request("/api/auth/erase-account", { method: "POST", body: JSON.stringify({ confirm_email: typed.trim() }) });
      await onAccountEnded("Your account has been erased.");
    } catch (error) {
      confirmErase.disabled = false;
      showError(error);
    }
  });

  confirmation.append(
    el("strong", { text: "Permanent action" }),
    el("p", { text: "Erasure cannot be undone. Your account is pseudonymised, external sign-in identities are removed, and private unfiled notes are scrubbed. Shared-project ownership must be handed over first." }),
    el("label", { className: "privacy-email", children: [el("span", { text: "Type your account email to confirm" }), emailInput] }),
    el("div", { className: "privacy-confirm-row", children: [confirmErase, cancelErase] }),
  );

  list.append(
    action(
      [el("strong", { text: "Download my data" }), el("span", { text: "Save a JSON copy of your personal data. This starts a download and deletes nothing." })],
      download,
    ),
    action(
      [el("strong", { text: "Sign out everywhere" }), el("span", { text: "End all browser sessions, including this one. You can sign in again later." })],
      signOut,
    ),
    action(
      [el("strong", { text: "Erase my account" }), el("span", { text: "Permanently pseudonymise your account and remove external identities." })],
      eraseToggle,
      "privacy-danger",
    ),
  );
  actions.append(list, confirmation, message);
  root.append(header, noticePanel(), actions);
}
