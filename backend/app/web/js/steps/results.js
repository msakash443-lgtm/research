import { el, label, emptyCard } from "../dom.js";
import { request, projectApi } from "../api.js";
import { filterByCitations, rankByCitations } from "../citationSortFilterLogic.js";
import { renderFrame } from "./frame.js";

const STATUS_LABEL = { found: "Found", missing: "Missing", not_searched: "Not searched", cannot_check: "Cannot check" };
// Sort/filter state for the raw search-result records box below (M2.13.2); module state, like the
// equivalent controls on the Sources screen (steps/sources.js).
let resultsCitationSort = false;
let resultsMinCitations = null;

function prismaBox(ctx) {
  const box = el("div", { className: "panel" });
  box.append(el("h2", { text: "PRISMA counts" }));
  const prisma = ctx.summary.prisma;
  if (!prisma) {
    box.append(el("p", { className: "muted small", text: "Not available." }));
    return box;
  }
  const id = prisma.identification;
  box.append(
    el("p", {
      text: `Identified: ${id.identified} · Duplicates removed: ${id.duplicates_removed} · After duplicates removed: ${id.after_duplicates_removed}`,
    })
  );
  const table = el("table", { className: "versions-table" });
  const head = el("tr");
  ["Database", "v", "Identified", "Capped", "Kind"].forEach((h) => head.append(el("th", { text: h })));
  table.append(head);
  (id.searches || []).forEach((search) => {
    const row = el("tr");
    row.append(
      el("td", { text: label(search.database) }),
      el("td", { text: String(search.version) }),
      el("td", { text: String(search.identified) }),
      el("td", { text: search.truncated ? "Capped" : "" }),
      el("td", { text: search.exact ? "Exact" : "Approximate" })
    );
    table.append(row);
  });
  box.append(table);
  if (prisma.warnings?.length) {
    const warnings = el("ul", { className: "warning-list" });
    prisma.warnings.forEach((w) => warnings.append(el("li", { text: w })));
    box.append(warnings);
  }
  box.append(el("p", { className: "muted small", text: "Screening counts appear once screening is built." }));
  return box;
}

function knownItemsBox(ctx) {
  const box = el("div", { className: "panel" });
  box.append(el("h2", { text: "Seed recall check" }));
  const seeds = ctx.summary.seeds || [];
  if (seeds.length === 0) {
    box.append(emptyCard("No seed papers", "Add seed papers in Criteria & seeds to check whether the search finds them."));
    return box;
  }
  const known = ctx.summary.knownItems;
  if (!known) {
    box.append(el("p", { className: "muted small", text: "Not available." }));
    return box;
  }
  const recall = known.recall_of_seeds == null ? "—" : `${Math.round(known.recall_of_seeds * 100)}%`;
  box.append(el("p", { text: `Found ${known.found} of ${known.checked} checked seeds (recall ${recall}).` }));
  if (known.flagged) {
    const warnings = el("ul", { className: "warning-list" });
    (known.flags || []).forEach((f) => warnings.append(el("li", { text: f })));
    box.append(warnings);
  }
  const list = el("ul", { className: "gate-list" });
  (known.items || []).forEach((item) => {
    const row = el("li", { className: "gate-row" });
    row.append(
      el("span", { className: "gate-title", text: item.title }),
      el("span", { className: "gate-status", text: STATUS_LABEL[item.status] || label(item.status) })
    );
    const note = item.reason || item.note;
    if (note) row.append(el("p", { className: "small muted gate-note", text: note }));
    list.append(row);
  });
  box.append(list);
  return box;
}

// Latest stored run per search (search_id+version come from the PRISMA box's own search list), cached
// on ctx.cache so toggling sort/filter below does not refetch. `min_citations=0` asks the server for
// every record (a threshold of 0 hides nothing, see app/citation_rank.py): the hiding/sorting itself
// is done client-side with citationSortFilterLogic.js, same rules as the server's.
// A failed request is reported as `failure` and is NOT cached, so the next render retries it; it is
// never shown as an empty result list.
async function fetchSearchResultRecords(ctx, searches) {
  ctx.cache = ctx.cache || {};
  ctx.cache.searchResults = ctx.cache.searchResults || {};
  const cache = ctx.cache.searchResults;
  const keyed = searches.map((s) => ({ s, key: `${s.search_id}:${s.version}` }));
  const missing = keyed.filter(({ key }) => !(key in cache));
  let failure = null;
  if (missing.length) {
    const fetched = await Promise.all(
      missing.map(({ s }) =>
        request(projectApi(ctx.project.id, `/searches/${s.search_id}/results?version=${s.version}&min_citations=0`)).then(
          (body) => ({ body }),
          (error) => ({ error })
        )
      )
    );
    missing.forEach(({ key }, i) => {
      if (fetched[i].error) failure = failure || fetched[i].error;
      else cache[key] = fetched[i].body;
    });
  }
  if (failure) return { records: [], failure };
  const records = keyed.flatMap(({ s, key }) => (cache[key]?.results || []).map((r) => ({ ...r, database: s.database })));
  return { records, failure: null };
}

// Raw search-result records ranked/filtered by citation count (M2.13.2), so a researcher can spot
// low-citation or unknown-citation hits before they are promoted to Sources. `rerender` redraws the
// whole step (cheap: records stay cached) so the toolbar reflects the new sort/filter state.
async function searchResultsBox(ctx, searches, rerender) {
  const box = el("div", { className: "panel" });
  box.append(el("h2", { text: "Search results by citation" }));
  const { records, failure } = await fetchSearchResultRecords(ctx, searches);
  if (failure) {
    box.append(el("p", { className: "warning-text", text: `Could not load the search results (${failure.message || "request failed"}). Reload this step to try again.` }));
    return box;
  }
  if (records.length === 0) {
    box.append(el("p", { className: "muted small", text: "No stored result records for these searches yet." }));
    return box;
  }
  const toolbar = el("div", { className: "source-actions" });
  const sortButton = el("button", {
    className: "quiet-button",
    text: "Sort by citations",
    attrs: { "aria-pressed": String(resultsCitationSort) },
  });
  sortButton.addEventListener("click", () => {
    resultsCitationSort = !resultsCitationSort;
    rerender();
  });
  toolbar.append(sortButton);
  const minCitationsInput = el("input", {
    attrs: { type: "number", min: "0", placeholder: "Min citations", "aria-label": "Minimum citations" },
  });
  if (resultsMinCitations != null) minCitationsInput.value = String(resultsMinCitations);
  minCitationsInput.addEventListener("change", () => {
    const value = minCitationsInput.value.trim();
    resultsMinCitations = value === "" ? null : Math.max(0, Math.trunc(Number(value)) || 0);
    rerender();
  });
  toolbar.append(minCitationsInput);
  box.append(toolbar);
  const ranked = resultsCitationSort
    ? rankByCitations(records, (r) => r.cited_by_count, resultsMinCitations)
    : filterByCitations(records, (r) => r.cited_by_count, resultsMinCitations);
  box.append(
    el("p", {
      className: "muted small",
      text: `${ranked.hiddenBelowThreshold} hidden below the citation threshold · ${ranked.unknownCount} unknown citation count · ${ranked.shown.length} of ${ranked.total} shown`,
    })
  );
  if (ranked.shown.length === 0) {
    box.append(emptyCard("No results match this filter", "Try a lower citation threshold."));
    return box;
  }
  const table = el("table", { className: "versions-table" });
  const head = el("tr");
  ["Title", "Database", "Year", "Citations"].forEach((h) => head.append(el("th", { text: h })));
  table.append(head);
  ranked.shown.forEach(({ item, unknown }) => {
    const row = el("tr");
    row.append(
      el("td", { text: item.title || "Untitled" }),
      el("td", { text: label(item.database) }),
      el("td", { text: item.year != null ? String(item.year) : "—" }),
      el("td", { text: unknown ? "Unknown" : String(item.cited_by_count) })
    );
    table.append(row);
  });
  box.append(table);
  return box;
}

async function render(root, ctx) {
  const searches = ctx.summary.prisma?.identification?.searches || [];
  if (searches.length === 0) {
    root.replaceChildren(emptyCard("No results yet", "Run a search on the Search step, then come back here to check the results."));
    return;
  }
  const rerender = () => {
    render(root, ctx);
  };
  renderFrame(root, ctx, {
    stepId: "results",
    work: [prismaBox(ctx), knownItemsBox(ctx), await searchResultsBox(ctx, searches, rerender)],
    next: ["retrieved"],
    stepNext: "sources",
  });
}

export default { render };
