// Pure logic for the account privacy screen (X.85). No DOM, no fetch, so it can be tested with
// `node --test backend/app/web/js/test`. The server still checks every rule these functions anticipate
// (POST /api/auth/erase-account compares the typed email the same way).

// `#/privacy` (optionally with a trailing slash). Account-level, not tied to a project.
export function parsePrivacyRoute(hash) {
  return /^#\/privacy\/?$/.test(String(hash || ""));
}

// Erasure needs the account's own email typed in. Same rule as the server: trimmed, case-insensitive.
export function confirmEmailMatches(typed, accountEmail) {
  if (!accountEmail || typeof typed !== "string") return false;
  const wanted = String(accountEmail).trim().toLowerCase();
  return wanted !== "" && typed.trim().toLowerCase() === wanted;
}

// Rows for the processor list from GET /api/privacy/processors. A processor with no host shows a dash.
export function processorRows(processors) {
  return (Array.isArray(processors) ? processors : []).map((processor) => ({
    key: processor.id || processor.label,
    label: processor.label || "Unnamed service",
    purpose: processor.purpose || "",
    dataSent: processor.data_sent || "",
    host: processor.host || "—",
  }));
}
