# Evaluation harness (plan X.5)

Measures the AI steps against the scholar's hand-labelled gold set (`backend/tests/gold`, X.15) using
the spec §11 targets, and makes sure no prompt change ships without a fresh measurement.

## Pieces

- `app/evaluation.py`: the metrics (pure functions).
  - Screening: Cohen's kappa ≥ 0.8 on the papers the AI decided. The share of "maybe" answers is
    reported next to it. The false-exclusion rate (gold *include* the AI suggested excluding) must be
    below 2%.
  - Extraction: field-level precision and recall ≥ 90% on the critical fields.
  - Any AI failure, or a metric that can't be computed, makes the result **incomplete**, never passed.
- `evals/run_eval.py`: runs the real prompt and model over the gold set and writes a report to
  `evals/reports/`. Reports hold paper ids and outcomes only, no titles or abstracts.
- `evals/screening_criteria.json`: the criteria the scholar screened the gold set with
  (`[{code, kind, text}]`). Empty until Q10 is answered.
- `evals/baselines.json`: one entry per prompt version the app uses, checked by
  `tests/test_eval_baselines.py`.

## The rule CI enforces

For every prompt version the app calls:

1. There must be an entry in `baselines.json`, and no entry for a prompt version the app no longer uses.
   A new prompt version therefore can't merge without a new entry.
2. `eval: "none"` needs a reason (there is no gold dataset type for that step yet).
3. `eval: "screening"`:
   - **While the gold set is empty:** status `unevaluated` with a reason.
   - **Once it has papers:** status `passed` or `failed`, plus a `report` path. That report must name
     this prompt version and carry the dataset hash of the *current* gold set and criteria. Changing
     the prompt, the gold set or the criteria therefore fails CI until someone re-runs the evaluation
     and updates the entry. A `failed` baseline also needs `accepted_by` and a `reason`: shipping a
     known regression is a person's decision.

CI never calls a model (no credentials, and it would cost tokens).

**Model changes:** CI can't see which model is deployed. Whoever changes `LLM_MODEL` re-runs the
evaluation and updates the entry; the report records `model_id`.

## Running

From `backend/`, with `LLM_API_BASE_URL`, `LLM_API_KEY` and `LLM_MODEL` set:

```bash
python -m evals.run_eval screening
```

Exit code 0 = passed, 1 = failed, 2 = incomplete. Commit the report and point the baseline entry at it.

Extraction has metrics but no runner yet: there is no AI extraction step until M3.3. Gap metrics wait
for the gap miner (M3.9) and a gold format for known gaps.
