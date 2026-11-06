// Pure request-building and response-parsing helpers for "Add my own paper" (M2.14.2): DOI/title
// lookup, manual entry, and BibTeX paste on the Sources screen. No DOM, no network: steps/sources.js
// wires these to `request()`/`fetch` calls and renders the result. Keeping this logic here, separate
// from the DOM wiring, is what makes it unit-testable (see test/ownPaperLogic.test.mjs).

// `SourceCreate` text fields the manual form sends besides `title` (handled separately: required,
// never dropped when blank).
const OPTIONAL_TEXT_FIELDS = ["doi", "url", "venue", "abstract", "oa_url"];

// Comma/semicolon-separated author names -> a clean array, or undefined when empty. `SourceCreate`
// treats `authors` as optional; sending `undefined` (omitted) rather than `[]` matches "not given".
export function parseAuthors(text) {
  const names = String(text || "")
    .split(/[,;]/)
    .map((name) => name.trim())
    .filter(Boolean);
  return names.length ? names : undefined;
}

// Builds the `POST .../sources/own` body from the manual-entry form fields (a plain object, e.g.
// from `Object.fromEntries(new FormData(form))`). Blank optional fields are dropped rather than
// sent as `""`, matching `SourceCreate`'s blank-rejection validator on its optional text fields.
export function buildManualOwnPaperPayload(fields = {}) {
  const title = String(fields.title || "").trim();
  if (!title) throw new Error("Give the paper's title.");
  const payload = { title };
  OPTIONAL_TEXT_FIELDS.forEach((key) => {
    const value = String(fields[key] || "").trim();
    if (value) payload[key] = value;
  });
  if (fields.source_type) payload.source_type = String(fields.source_type).trim();
  const authors = parseAuthors(fields.authors);
  if (authors) payload.authors = authors;
  if (fields.year !== undefined && fields.year !== null && String(fields.year).trim() !== "") {
    const year = Number(fields.year);
    if (!Number.isInteger(year)) throw new Error("Year must be a whole number.");
    payload.year = year;
  }
  return payload;
}

// Builds the `POST .../sources/own` body for a pasted BibTeX entry.
export function buildBibtexOwnPaperPayload(text) {
  const bibtex = String(text || "").trim();
  if (!bibtex) throw new Error("Paste one BibTeX entry first.");
  return { bibtex };
}

// Builds the `POST .../sources/own/lookup` body: exactly one of a DOI or a title. The server
// rejects both and neither with the same 422 message; checking it client-side first saves a round
// trip and gives the same wording either way.
export function buildOwnPaperLookupPayload({ doi, title } = {}) {
  const cleanDoi = String(doi || "").trim();
  const cleanTitle = String(title || "").trim();
  if (Boolean(cleanDoi) === Boolean(cleanTitle)) {
    throw new Error("Give either a DOI or a title, not both or neither.");
  }
  return cleanDoi ? { doi: cleanDoi } : { title: cleanTitle };
}

// Turns a resolved `OwnPaperLookupRead` (status included/not_found/failed) into a short message for
// the panel. `row === null` is the state before the first row has arrived (just queued, or a 404
// while the task is still queued/running): shown as "pending" so the caller keeps polling.
export function describeOwnPaperLookup(row) {
  if (!row) return { kind: "pending", text: "Looking…" };
  if (row.status === "included") return { kind: "included", text: "Added to your sources." };
  if (row.status === "not_found") {
    return { kind: "not_found", text: row.detail || "No matching paper was found by any enabled lookup service." };
  }
  return { kind: "failed", text: row.detail || "The lookup failed." };
}

// The state when the panel stops polling (poll budget used up) while the task still has no resolved row.
// It is still pending, not failed: the server's own row (or the terminal `failed` row it writes when retries
// run out) is the only truth, so the person checks again rather than being told it failed or to come back later.
export function describePollBudgetExhausted(taskId) {
  return { taskId, kind: "pending", text: "Still queued or running. Check again to see the result." };
}

// Whether a `GET .../own/lookup?task_id=` error means "not resolved yet, poll again" (404, the only
// status that endpoint ever returns for an unresolved task) rather than a genuine failure.
export function isLookupPending(error) {
  return Boolean(error) && error.status === 404;
}
