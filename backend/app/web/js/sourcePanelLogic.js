// Source panel helpers for the write step (X.31.10). Pure: no DOM, no network, no ProseMirror import.
// The rules here are heuristics for the person's eyes only. A "no source linked yet" flag is a prompt, never a
// verdict, and nothing here decides whether a citation is valid (the server does that, app/routers/documents.py).

// The `@` picker and the Sources list offer only sources a person has verified. Unverified ones never appear here.
export function citableSources(sources, query = "") {
  const needle = String(query || "").trim().toLowerCase();
  return (sources || [])
    .filter((source) => source && source.metadata_verified && !source.merged_into)
    .filter((source) => {
      if (!needle) return true;
      const haystack = [source.title, source.year, ...(source.authors || [])].join(" ").toLowerCase();
      return haystack.includes(needle);
    })
    .sort((a, b) => String(a.title || "").localeCompare(String(b.title || "")));
}

// The label used for a source everywhere in the panel: first author's surname and year when known, else the title.
export function sourceLabel(source) {
  const first = (source.authors || [])[0] || "";
  const surname = String(first).trim().split(/\s+/).pop() || "";
  if (surname && source.year) return `${surname} ${source.year}`;
  return source.title || "Untitled source";
}

// Sentence ranges in a flat textblock string. `flat` uses one character per ProseMirror position: text as-is,
// a citation atom as CITE (U+FFFC), a hard break as "\n". So the returned offsets can be added to the block's
// start position directly. A sentence ends at ., ! or ? followed by whitespace or the end, or at a line break.
export const CITE_CHAR = "\uFFFC";

export function sentenceRanges(flat) {
  const ranges = [];
  let start = 0;
  const push = (from, to) => {
    let s = from;
    let e = to;
    while (s < e && /\s/.test(flat[s])) s += 1;
    while (e > s && /\s/.test(flat[e - 1])) e -= 1;
    if (e > s) ranges.push({ from: s, to: e });
  };
  for (let i = 0; i < flat.length; i += 1) {
    const ch = flat[i];
    const endsHere = ch === "\n" || ((ch === "." || ch === "!" || ch === "?") && (i + 1 === flat.length || /\s/.test(flat[i + 1])));
    if (endsHere) {
      push(start, i + 1);
      start = i + 1;
    }
  }
  push(start, flat.length);
  return ranges;
}

// A sentence "states a number" when it has a digit or a percent sign. Flagged when it states one and has no citation
// in it. Returns offsets (same units as `flat`) for the editor to underline.
export function unlinkedStatements(flat) {
  return sentenceRanges(flat)
    .filter(({ from, to }) => {
      const text = flat.slice(from, to);
      return /[0-9%]/.test(text) && !text.includes(CITE_CHAR);
    });
}

// The sources a selection relies on: each citation atom's source id, in order, without repeats.
export function citedIdsIn(sourceIds) {
  const seen = new Set();
  const ids = [];
  for (const id of sourceIds || []) {
    if (!id || seen.has(id)) continue;
    seen.add(id);
    ids.push(id);
  }
  return ids;
}

// The `@` picker: verified sources matching what has been typed after the "@", at most `limit`.
export function pickerMatches(sources, query, limit = 8) {
  return citableSources(sources, query).slice(0, limit);
}
