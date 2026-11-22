// D0 reference page (X.242): renders one sample template per theme/mode and lists every semantic token.
const THEMES = ["scholarly", "modern", "dense"];
const MODES = ["light", "dark"];
const template = document.getElementById("sample");
const root = document.documentElement;

function sample(theme, mode) {
  const node = template.content.firstElementChild.cloneNode(true);
  node.dataset.theme = theme;
  node.dataset.mode = mode;
  return node;
}

function renderStage(theme, mode) {
  root.dataset.theme = theme;
  root.dataset.mode = mode;
  const stage = document.getElementById("stage");
  stage.replaceChildren(sample(theme, mode));
  const list = document.getElementById("tokens");
  list.replaceChildren();
  const style = getComputedStyle(root);
  const names = [];
  for (const sheet of document.styleSheets) {
    for (const rule of sheet.cssRules) {
      if (!rule.selectorText || !rule.selectorText.startsWith('[data-theme="' + theme + '"]')) continue;
      for (const prop of rule.style) if (prop.startsWith("--") && !names.includes(prop)) names.push(prop);
    }
  }
  for (const name of names) {
    const item = document.createElement("div");
    item.className = "ds-token";
    if (name.startsWith("--color-")) {
      const swatch = document.createElement("span");
      swatch.className = "ds-swatch";
      swatch.style.background = "var(" + name + ")";
      item.append(swatch);
    }
    item.append(name + ": " + style.getPropertyValue(name).trim());
    list.append(item);
  }
}

const themeSelect = document.getElementById("theme");
const modeSelect = document.getElementById("mode");
const stored = (() => { try { return JSON.parse(localStorage.getItem("ds-preview") || "{}"); } catch { return {}; } })();
themeSelect.value = THEMES.includes(stored.theme) ? stored.theme : "scholarly";
modeSelect.value = MODES.includes(stored.mode) ? stored.mode : (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
function apply() {
  try { localStorage.setItem("ds-preview", JSON.stringify({ theme: themeSelect.value, mode: modeSelect.value })); } catch { /* storage unavailable */ }
  renderStage(themeSelect.value, modeSelect.value);
}
themeSelect.addEventListener("change", apply);
modeSelect.addEventListener("change", apply);

const compare = document.getElementById("compare");
for (const theme of THEMES) for (const mode of MODES) {
  const cell = document.createElement("div");
  cell.className = "ds-cell";
  cell.dataset.theme = theme;
  cell.dataset.mode = mode;
  const label = document.createElement("p");
  label.className = "ds-label";
  label.textContent = theme + " / " + mode;
  cell.append(label, sample(theme, mode).querySelector(".stack"));
  compare.append(cell);
}
apply();
