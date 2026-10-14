// The writing editor's source flags (X.31.10). A sentence that states a number (a digit or a percent sign) and has no
// citation gets a faint dotted underline with the hint "No source linked yet". It is a heuristic, not an AI check, and
// nothing is saved: the flags are decorations only. Typing "@" calls `onAt` so the panel can offer verified sources.
import * as PM from "../../vendor/prosemirror/prosemirror.bundle.js";
import { CITE_CHAR, unlinkedStatements } from "../sourcePanelLogic.js";

const { Plugin, PluginKey } = PM.state;
const { Decoration, DecorationSet } = PM.view;

export const sourceFlagsKey = new PluginKey("sourceFlags");
export const NO_SOURCE_HINT = "No source linked yet";

// One character per ProseMirror position: text as-is, a citation or any other leaf as CITE_CHAR, a hard break as "\n".
export function flatten(textblock) {
  let flat = "";
  textblock.forEach((child) => {
    if (child.isText) flat += child.text;
    else if (child.type.name === "hard_break") flat += "\n";
    else flat += CITE_CHAR;
  });
  return flat;
}

export function unlinkedRanges(doc) {
  const found = [];
  doc.descendants((node, pos) => {
    if (!node.isTextblock) return true;
    const flat = flatten(node);
    for (const { from, to } of unlinkedStatements(flat)) {
      found.push({ from: pos + 1 + from, to: pos + 1 + to, text: flat.slice(from, to).split(CITE_CHAR).join("[citation]") });
    }
    return false;
  });
  return found;
}

function flagDecorations(doc) {
  const decorations = unlinkedRanges(doc).map(({ from, to }) =>
    Decoration.inline(from, to, { class: "unlinked-statement", title: NO_SOURCE_HINT, "aria-description": NO_SOURCE_HINT }));
  return DecorationSet.create(doc, decorations);
}

export function sourceFlagsPlugin({ onAt } = {}) {
  return new Plugin({
    key: sourceFlagsKey,
    state: {
      init: (_, { doc }) => flagDecorations(doc),
      apply: (tr, previous) => (tr.docChanged ? flagDecorations(tr.doc) : previous),
    },
    props: {
      decorations(state) {
        return sourceFlagsKey.getState(state);
      },
      handleTextInput(view, _from, _to, text) {
        // Let the "@" be typed; the panel reads the character before the cursor when a source is picked.
        if (text === "@" && onAt) setTimeout(() => onAt(view), 0);
        return false;
      },
    },
  });
}
