// Small DOM helpers shared by every step module. No framework; CSP forbids inline script/style,
// so every handler here is attached with addEventListener/.onclick, never onclick="" attributes.

export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

export function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.add("show");
  window.setTimeout(() => node.classList.remove("show"), 3200);
}

// Runs a form's save once at a time: the submit button is disabled until the save finishes,
// so a double click or a slow network can't create the same record twice.
export async function saveOnce(form, save) {
  const button = form.querySelector('button[type="submit"]');
  if (button.disabled) return;
  button.disabled = true;
  try {
    await save();
  } finally {
    button.disabled = false;
  }
}

export function label(value) {
  return String(value).replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function el(tag, options = {}) {
  const node = document.createElement(tag);
  if (options.className) node.className = options.className;
  if (options.text !== undefined) node.textContent = options.text;
  if (options.type) node.type = options.type;
  if (options.href) node.href = options.href;
  if (options.target) node.target = options.target;
  if (options.rel) node.rel = options.rel;
  if (options.attrs) {
    for (const [name, value] of Object.entries(options.attrs)) node.setAttribute(name, value);
  }
  if (options.children) node.append(...options.children);
  return node;
}

export function emptyCard(title, description) {
  const card = el("div", { className: "empty-card" });
  card.append(el("h3", { text: title }), el("p", { text: description }));
  return card;
}

export function formatDate(value) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? "" : date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

// Promise-based confirm using the single static #confirm-dialog, instead of window.confirm
// (which a CSP-safe app can still use, but a styled dialog matches the rest of the UI and lets
// the message explain the consequence, e.g. "work waiting on this gate will start").
export function confirmAction(title, message, confirmLabel = "Confirm") {
  return new Promise((resolve) => {
    const dialog = $("#confirm-dialog");
    $("#confirm-title").textContent = title;
    $("#confirm-message").textContent = message;
    const okButton = $("#confirm-ok");
    okButton.textContent = confirmLabel;
    let settled = false;
    const finish = (result) => {
      if (settled) return;
      settled = true;
      okButton.removeEventListener("click", onOk);
      dialog.removeEventListener("close", onClose);
      resolve(result);
    };
    const onOk = () => {
      finish(true);
      dialog.close();
    };
    const onClose = () => finish(false);
    okButton.addEventListener("click", onOk);
    dialog.addEventListener("close", onClose);
    dialog.showModal();
  });
}
