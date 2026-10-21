// The writing editor's document schema (X.31.4). Mirrors app/ledger.py: the same node and mark names, so the JSON
// this editor saves passes `ledger.validate_schema`. Provenance lives on one `prov` mark per text run (provenance.js).
import * as PM from "../../vendor/prosemirror/prosemirror.bundle.js";

const { Schema } = PM.model;

export const schema = new Schema({
  nodes: {
    doc: { content: "block+" },
    paragraph: {
      content: "inline*",
      group: "block",
      parseDOM: [{ tag: "p" }],
      toDOM: () => ["p", 0],
    },
    heading: {
      attrs: { level: { default: 2 } },
      content: "inline*",
      group: "block",
      defining: true,
      parseDOM: [{ tag: "h2", attrs: { level: 2 } }, { tag: "h3", attrs: { level: 3 } }],
      toDOM: (node) => [`h${node.attrs.level}`, 0],
    },
    blockquote: {
      content: "block+",
      group: "block",
      defining: true,
      parseDOM: [{ tag: "blockquote" }],
      toDOM: () => ["blockquote", 0],
    },
    bullet_list: {
      content: "list_item+",
      group: "block",
      parseDOM: [{ tag: "ul" }],
      toDOM: () => ["ul", 0],
    },
    ordered_list: {
      content: "list_item+",
      group: "block",
      parseDOM: [{ tag: "ol" }],
      toDOM: () => ["ol", 0],
    },
    list_item: {
      content: "paragraph block*",
      defining: true,
      parseDOM: [{ tag: "li" }],
      toDOM: () => ["li", 0],
    },
    text: { group: "inline" },
    hard_break: {
      inline: true,
      group: "inline",
      selectable: false,
      parseDOM: [{ tag: "br" }],
      toDOM: () => ["br"],
    },
    // A citation to a verified project source. An atom: it cannot be typed into, only inserted from the source panel.
    cite: {
      inline: true,
      group: "inline",
      atom: true,
      selectable: true,
      attrs: { source_id: { default: "" } },
      parseDOM: [{ tag: "span[data-source-id]", getAttrs: (dom) => ({ source_id: dom.getAttribute("data-source-id") || "" }) }],
      toDOM: (node) => ["span", { class: "cite", "data-source-id": node.attrs.source_id }],
    },
    // A number computed by an analysis run (M6.3). An atom: it cannot be typed into, only inserted from a verified
    // AnalysisRun output. `path` is a dot-separated walk through that run's output_json (app/number_citation.py).
    stat: {
      inline: true,
      group: "inline",
      atom: true,
      selectable: true,
      // `value` is a display cache only, set from the number panel's already-fetched value (numberPanelLogic.js
      // ::statAttrs) so the live editor can show what was inserted without a network round trip. It is never the
      // source of truth: ledger.check_integrity/manuscript_export always resolve the real value from the run's
      // output_json by `analysis_run_id`+`path` (app/number_citation.py), both at save time and at export time.
      attrs: { analysis_run_id: { default: "" }, path: { default: "" }, value: { default: null } },
      parseDOM: [{
        tag: "span[data-analysis-run-id]",
        getAttrs: (dom) => {
          const raw = dom.getAttribute("data-value");
          const value = raw === null || raw === "" ? null : Number(raw);
          return {
            analysis_run_id: dom.getAttribute("data-analysis-run-id") || "",
            path: dom.getAttribute("data-path") || "",
            value: Number.isFinite(value) ? value : null,
          };
        },
      }],
      toDOM: (node) => ["span", {
        class: "stat",
        "data-analysis-run-id": node.attrs.analysis_run_id,
        "data-path": node.attrs.path,
        ...(node.attrs.value === null || node.attrs.value === undefined ? {} : { "data-value": node.attrs.value }),
      }],
    },
  },
  marks: {
    em: { parseDOM: [{ tag: "em" }, { tag: "i" }], toDOM: () => ["em", 0] },
    strong: { parseDOM: [{ tag: "strong" }, { tag: "b" }], toDOM: () => ["strong", 0] },
    // Authorship. `inclusive: false` so text typed at the edge of a span does not silently join it; provenance.js
    // decides which span typed text belongs to. HTML from outside never carries a prov mark (no parseDOM).
    prov: {
      attrs: {
        sid: { default: "" },
        origin: { default: "typed" },
        by: { default: "" },
        sug: { default: null },
        note: { default: null },
        paste: { default: null },
        src: { default: null },
      },
      inclusive: false,
      toDOM: (mark) => ["span", { class: `prov prov-${mark.attrs.origin}`, "data-sid": mark.attrs.sid }, 0],
    },
  },
});
