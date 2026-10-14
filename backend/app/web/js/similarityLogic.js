// Similarity flags for the write step (X.31.14). Pure: no DOM, no network. The server decides what is flagged
// (app/similarity.py); this file only words the flags and slices the section's saved text at the flagged offsets.
// Nothing here is a verdict: each flag is a prompt to quote with a citation or to rephrase.

const SOURCE_FALLBACK = "a project source";

export function hintFor(kind, sourceTitle) {
  const source = sourceTitle || SOURCE_FALLBACK;
  if (kind === "near_verbatim") return `Very close to ${source}. Quote it with a citation, or put it in your own words.`;
  if (kind === "close_paraphrase") return `Close to ${source}. Quote it with a citation, or put it in your own words.`;
  if (sourceTitle) return `Quoted from ${sourceTitle} without a citation. Add a citation in the same paragraph, or put it in your own words.`;
  return "Quoted without a citation. Add a citation in the same paragraph, or put it in your own words.";
}

// `state` says what the panel can show: "none" (never checked), "older" (checked a version that is no longer the
// latest saved one, so its offsets don't match this text), or "current" (flags, each with the sentence it points at).
export function flagsFor(check, latestVersionId, latestText) {
  if (!check) return { state: "none", flags: [] };
  if (check.version_id !== latestVersionId) return { state: "older", flags: [] };
  const flags = check.findings.map((finding) => ({
    quote: latestText.slice(finding.start, finding.end),
    kind: finding.kind,
    hint: hintFor(finding.kind, finding.source_title),
  }));
  return { state: "current", flags };
}

const PART_LABELS = {
  near_verbatim: ["very close", "very close"],
  close_paraphrase: ["close paraphrase", "close paraphrases"],
  quote_without_citation: ["quoted without a citation", "quoted without a citation"],
};

export function summaryLine(summary) {
  const parts = [];
  let total = 0;
  for (const [kind, [one, many]] of Object.entries(PART_LABELS)) {
    const count = summary[kind] || 0;
    total += count;
    if (count) parts.push(`${count} ${count === 1 ? one : many}`);
  }
  if (!total) return "No flags.";
  return `${total} ${total === 1 ? "flag" : "flags"}: ${parts.join(", ")}`;
}

// The institutional check button (X.31.15). The server says whether a connector exists; until one does the button is
// disabled with the fixed hint. A missing list (the request failed) is treated as not connected.
const NOT_CONNECTED = "Not connected: ask your institution";

export function institutionalCheck(providers) {
  const entry = (providers || []).find((provider) => provider.id === "institutional");
  const available = Boolean(entry && entry.available);
  return { label: entry ? entry.label : "Institutional check", disabled: !available, hint: available ? "" : (entry && entry.hint) || NOT_CONNECTED };
}
