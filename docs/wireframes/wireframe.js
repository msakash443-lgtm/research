// X.14 wireframe helpers shared by every screen: question-pin toggle and toast.
// Plain script (no modules) so the pages open straight from disk (file://).
(function () {
  const KEY = "x14-hide-pins";
  function applyPins(hide) {
    document.body.classList.toggle("hide-pins", hide);
    const box = document.getElementById("pin-toggle");
    if (box) box.checked = !hide;
  }
  document.addEventListener("DOMContentLoaded", () => {
    let hide = false;
    try { hide = localStorage.getItem(KEY) === "1"; } catch (e) { /* storage blocked: show pins */ }
    applyPins(hide);
    const box = document.getElementById("pin-toggle");
    if (box) box.addEventListener("change", () => {
      applyPins(!box.checked);
      try { localStorage.setItem(KEY, box.checked ? "0" : "1"); } catch (e) { /* ignore */ }
    });
  });

  let timer = null;
  window.wfToast = function (text) {
    let node = document.getElementById("wf-toast");
    if (!node) {
      node = document.createElement("div");
      node.id = "wf-toast";
      node.className = "toast";
      node.setAttribute("role", "status");
      document.body.appendChild(node);
    }
    node.textContent = text;
    node.hidden = false;
    clearTimeout(timer);
    timer = setTimeout(() => { node.hidden = true; }, 3200);
  };

  // True when focus is in a text field, so single-key shortcuts don't fire while typing.
  window.wfTyping = function (event) {
    const t = event.target;
    return t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable);
  };
})();
