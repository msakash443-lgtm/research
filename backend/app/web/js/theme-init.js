// X.245: theme + mode bootstrap. A classic (non-module) script loaded blocking in <head>, so the attributes are set
// before first paint (no flash) and the strict CSP (script-src 'self') needs no inline script. The server renders the
// default theme into <html data-theme> and the registered ones into data-themes; the per-user choice lives in
// localStorage. app.js uses the same RaTheme object for the topbar controls.
(function (root) {
  var THEME_KEY = "ra.theme";
  var MODE_KEY = "ra.mode";

  function pickTheme(saved, available, fallback) {
    return available.indexOf(saved) >= 0 ? saved : fallback;
  }
  function pickMode(saved, prefersDark) {
    if (saved === "light" || saved === "dark") return saved;
    return prefersDark ? "dark" : "light";
  }
  function read(storage, key) {
    try { return storage.getItem(key); } catch (e) { return null; }
  }
  function write(storage, key, value) {
    try { storage.setItem(key, value); } catch (e) { /* blocked site data: the choice lasts for this page only */ }
  }
  function available(doc) {
    return (doc.documentElement.getAttribute("data-themes") || "").split(" ").filter(Boolean);
  }
  function apply(doc, storage, matchMedia) {
    var html = doc.documentElement;
    var themes = available(doc);
    var fallback = html.getAttribute("data-theme");
    html.setAttribute("data-theme", pickTheme(read(storage, THEME_KEY), themes, fallback));
    var dark = !!(matchMedia && matchMedia("(prefers-color-scheme: dark)").matches);
    html.setAttribute("data-mode", pickMode(read(storage, MODE_KEY), dark));
  }
  function setTheme(doc, storage, name) {
    if (available(doc).indexOf(name) < 0) return false;
    doc.documentElement.setAttribute("data-theme", name);
    write(storage, THEME_KEY, name);
    return true;
  }
  function setMode(doc, storage, mode) {
    if (mode !== "light" && mode !== "dark") return false;
    doc.documentElement.setAttribute("data-mode", mode);
    write(storage, MODE_KEY, mode);
    return true;
  }

  // The page's own controls: bound to the real document and storage so app.js never touches localStorage itself.
  function bind(doc, storage) {
    return {
      available: function () { return available(doc); },
      setTheme: function (name) { return setTheme(doc, storage, name); },
      setMode: function (mode) { return setMode(doc, storage, mode); }
    };
  }

  root.RaTheme = { pickTheme: pickTheme, pickMode: pickMode, apply: apply, bind: bind };
  if (root.document) {
    var storage;
    try { storage = root.localStorage; } catch (e) { storage = { getItem: function () { return null; }, setItem: function () {} }; }
    apply(root.document, storage, root.matchMedia && root.matchMedia.bind(root));
    root.RaTheme.page = bind(root.document, storage);
  }
})(globalThis);
