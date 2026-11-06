# Gold set (plan X.15)

Scholar-labelled ground truth for measuring AI screening/extraction (M1.8.2–M1.8.3, M2.9, X.5).

**Status: empty.** Topic and papers are pending Q10. Nothing here may be invented or AI-labelled.

Target: ~20 known-relevant papers, ~50 hand-screened (include/exclude + reason), a few verified
extractions (each field needs a `source_quote`).

Scholars: give them `SCHOLAR_INSTRUCTIONS.md`, `gold_set_template.csv` (one row per paper) and
`gold_extractions_template.csv` (optional; one row per extracted field, each with a `source_quote`).

Workflow: scholar fills the CSVs -> `loader.csv_to_gold_set(papers_csv, topic, supplied_by,
extractions_csv=...)` -> write `gold_set.json` -> `pytest backend/tests/gold` validates it.
