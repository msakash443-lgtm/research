// Wording for the full-text coverage indicator (X.76). Pure: no DOM, no network. The server decides the level
// (app/fulltext_coverage.py); this only words it and says what that means for the decision being made.

const LEVELS = {
  full_text: { text: "Full text available", tone: "good", advice: "The stored PDF is available to check against." },
  abstract_only: { text: "Abstract only", tone: "warn", advice: "Decide from the abstract with care: the full text is not stored." },
  not_accessible: { text: "Not accessible", tone: "bad", advice: "Neither the full text nor an abstract is stored: only the title is known." },
};

export function coverageSummary(coverage) {
  const known = coverage && LEVELS[coverage.level];
  if (!known) return { text: "Coverage unknown", tone: "warn", advice: "", reason: null, link: null };
  const link = coverage.open_access_url && /^https?:\/\//i.test(coverage.open_access_url) ? coverage.open_access_url : null;
  return { ...known, reason: coverage.reason || null, link };
}
