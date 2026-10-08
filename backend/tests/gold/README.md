# Gold set (plan X.15)

Scholar-labelled ground truth for measuring AI screening/extraction (M1.8.2–M1.8.3, M2.9, X.5).

**Status: empty.** Topic and papers are pending Q10. Nothing here may be invented or AI-labelled.

Target: ~20 known-relevant papers, ~50 hand-screened (include/exclude + reason), a few verified
extractions (each field needs a `source_quote`).

Workflow: scholar fills `gold_set_template.csv` -> `loader.csv_to_gold_set(...)` -> write `gold_set.json`
(add `extraction` objects by hand) -> `pytest backend/tests/gold` validates it.
