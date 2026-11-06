# Gold set: instructions for the scholar

You are giving us the "answer key" we use to check how well the AI screens papers. Only your own judgement goes in this file. Nobody, including the AI, fills in or edits your answers.

## What you need
- The file `gold_set_template.csv` (open it in Excel or Google Sheets; keep the first row as it is).
- About 50 papers from your field. At least about 20 of them should be papers you already know are relevant. Include some papers you would reject, so the AI is tested on those as well.

## One row per paper

| Column | What to write | Required |
|---|---|---|
| `id` | A short unique code you choose, e.g. `p01`, `p02`. No two rows may share an id. | yes |
| `doi` | The paper's DOI, e.g. `10.1000/xyz123`. Leave empty if it has none. | no |
| `title` | The full title. | yes |
| `abstract` | The abstract, pasted as it is. Leave empty if you have none. | no |
| `label` | `include` or `exclude`. Nothing else. | yes |
| `known_relevant` | `yes` for the roughly 20 papers you already know are relevant. `no` for all others. | yes |
| `reason` | One sentence on why you included or excluded it, e.g. "wrong population: children only". | yes |

## Rules
1. Judge each paper yourself. Do not ask an AI to label them.
2. A paper with `known_relevant` = `yes` should normally have `label` = `include`.
3. Save as **CSV (UTF-8)**. In Excel use "CSV UTF-8 (Comma delimited)". Other CSV formats can garble accented characters.
4. Send the saved file back to the developer. Do not change the column names.


## Optional: extraction examples
For a few papers, you can record values you would extract, using `gold_extractions_template.csv`. One row per value:

| Column | What to write |
|---|---|
| `id` | The same id as in the papers file. |
| `field` | What you extracted, e.g. `sample_size`. |
| `value` | The value, e.g. `120`. |
| `source_quote` | The exact words from the paper that show it. Required; a value without a quote is rejected. |

## Still open
- The topic and the "supplied by" name that go into `gold_set.json` are not decided yet. We need both from you.

## What happens next (the developer does this)
The developer converts your CSV files into `gold_set.json` using your topic and your name, then runs `pytest backend/tests/gold` to check the file is valid.
