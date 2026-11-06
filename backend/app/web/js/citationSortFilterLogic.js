// Pure citation-count sort/filter for the Sources and Results-check screens (M2.13.2). Mirrors
// app/citation_rank.py::rank's rules so the UI's hidden/unknown counts match what the server
// computes for the equivalent `min_citations` threshold:
//   * a missing count is "unknown", never 0, and unknown items are never hidden by the threshold;
//   * a known count below the threshold is hidden, and the caller is told how many;
//   * sorting (when asked for) puts the highest count first, unknown counts last; ties keep the
//     input order (both the filter and the sort below are stable).
// No DOM, no network: callers pass the array, a `countOf` accessor and the threshold.

function tag(items, countOf) {
  return (items || []).map((item) => ({ item, unknown: countOf(item) == null }));
}

// Hides known counts below `threshold` (null/undefined threshold hides nothing); keeps input order.
export function filterByCitations(items, countOf, threshold) {
  const rows = tag(items, countOf);
  const shown = [];
  let hiddenBelowThreshold = 0;
  for (const row of rows) {
    if (!row.unknown && threshold != null && countOf(row.item) < threshold) {
      hiddenBelowThreshold += 1;
      continue;
    }
    shown.push(row);
  }
  return {
    shown,
    total: rows.length,
    hiddenBelowThreshold,
    unknownCount: rows.filter((r) => r.unknown).length,
  };
}

// Same as `filterByCitations`, then sorts the kept known-count rows highest-first and moves the
// unknown ones after them (counts and hidden/unknown totals are unaffected by the reordering).
export function rankByCitations(items, countOf, threshold) {
  const filtered = filterByCitations(items, countOf, threshold);
  const known = filtered.shown.filter((r) => !r.unknown).sort((a, b) => countOf(b.item) - countOf(a.item));
  const unknown = filtered.shown.filter((r) => r.unknown);
  return { ...filtered, shown: [...known, ...unknown] };
}
