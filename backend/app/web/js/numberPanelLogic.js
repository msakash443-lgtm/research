// Number panel helpers for the write step (M6.3.1). Pure: no DOM, no network, no ProseMirror import. Mirrors
// sourcePanelLogic.js's shape for the `@` citation picker, but for `stat` nodes: a number is never typed, only
// inserted from one of this project's AnalysisRun outputs (app/number_citation.py resolves and enforces it server
// side; this file only decides what the panel shows and searches).

// `options` is the flattened list the server returns (GET .../documents/numbers): [{analysis_run_id, path, value}].
// Matches a free-text query against the path (e.g. "age.mean") or the run id, case-insensitively.
export function matchingNumbers(options, query = "") {
  const needle = String(query || "").trim().toLowerCase();
  return (options || [])
    .filter((option) => option && option.path && option.analysis_run_id)
    .filter((option) => !needle || `${option.path} ${option.analysis_run_id}`.toLowerCase().includes(needle))
    .sort((a, b) => a.path.localeCompare(b.path) || a.analysis_run_id.localeCompare(b.analysis_run_id));
}

// The label shown for one number: its path and a short form of its value (ints plain, floats to 4 significant
// decimal places, trimmed of trailing zeros) so "0.333333333" reads as "0.3333" in the list.
export function numberLabel(option) {
  const value = option.value;
  const text = Number.isInteger(value) ? String(value) : value.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
  return `${option.path} = ${text}`;
}

// A short label for the run a number came from, when more than one run is in the list: the first 8 characters of
// its id, since runs have no human name (app/models.py::AnalysisRun has none).
export function runLabel(analysisRunId) {
  return String(analysisRunId || "").slice(0, 8);
}

// The attrs for a new `stat` node (editor/schema.js) to insert for one chosen number. `value` is a display cache
// only (editor/schema.js::stat); the ledger and exports always resolve the real value from the run's output.
export function statAttrs(option) {
  return { analysis_run_id: option.analysis_run_id, path: option.path, value: option.value };
}
