// Wording for the "AI accuracy" panel (X.80). Pure: no DOM, no network. The server computes every figure
// (app/accuracy_report.py); this only turns them into plain sentences and says when there is nothing to show.

export const STAGE_NAMES = { title_abstract: "Title and abstract", full_text: "Full text" };

export function pct(value) {
  return value === null || value === undefined ? "not enough data" : `${Math.round(value * 100)}%`;
}

// One line per screening stage that has at least one comparable decision.
export function screeningLines(screening) {
  return Object.entries(screening)
    .filter(([, stage]) => stage.n > 0)
    .map(([key, stage]) => {
      const parts = [
        `${stage.n} paper(s) a person decided`,
        `of the ones you included, the AI also suggested including ${pct(stage.recall)} (${pct(stage.recall_not_excluded)} counting "maybe")`,
        `of the ones the AI suggested including, you included ${pct(stage.precision)}`,
      ];
      if (stage.false_exclusions > 0) parts.push(`${stage.false_exclusions} paper(s) you included were suggested for exclusion`);
      if (stage.skipped_human_maybe > 0) parts.push(`${stage.skipped_human_maybe} marked "maybe" by you were not counted`);
      return { stage: STAGE_NAMES[key] || key, text: parts.join(" · ") };
    });
}

// Per schema: one row per field, critical fields first, then by name.
export function extractionTables(extraction) {
  return Object.entries(extraction.by_schema).map(([schema, scored]) => ({
    schema,
    papers: scored.papers,
    rows: Object.entries(scored.fields)
      .map(([field, s]) => ({ field, critical: s.critical, precision: pct(s.precision), recall: pct(s.recall) }))
      .sort((a, b) => Number(b.critical) - Number(a.critical) || a.field.localeCompare(b.field)),
  }));
}

export function extractionNotes(extraction) {
  const notes = [];
  if (extraction.ai_extractions === 0) notes.push("No AI extractions in this project yet.");
  if (extraction.not_verified_yet > 0) notes.push(`${extraction.not_verified_yet} AI extraction(s) are not verified by a person yet, so they are not counted.`);
  if (extraction.no_ai_original_kept > 0) notes.push(`${extraction.no_ai_original_kept} verified AI extraction(s) were made before the AI's first values were kept, so they cannot be scored.`);
  if (extraction.old_schema > 0) notes.push(`${extraction.old_schema} use an old schema version and are not counted.`);
  return notes;
}

export function hasAnything(report) {
  return screeningLines(report.screening).length > 0 || extractionTables(report.extraction).length > 0;
}
