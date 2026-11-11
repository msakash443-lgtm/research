"""Run an AI step over the gold set and write a report (plan X.5).

    python -m evals.run_eval screening            # from backend/, with LLM_* configured
    python -m evals.run_eval screening --gold path/to/gold_set.json --criteria path/to/criteria.json
    python -m evals.run_eval extraction --schema default

This calls the configured model for every gold paper, so it is run by a person (it costs tokens and
needs credentials); CI never runs it. CI checks instead that `baselines.json` points at a report made
from the *current* gold set and the *current* prompt version (tests/test_eval_baselines.py).

Exit code: 0 passed, 1 failed, 2 incomplete (an AI failure or a metric that can't be computed).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.agent.llm import LLMConfigurationError, LLMResponseError, OpenAICompatibleLLM
from app.ai_extraction import AI_EXTRACTION_PROMPT, MAX_PROMPT_TEXT_CHARS, AiExtractionError, extract_with_llm
from app.config import get_settings
from app.evaluation import INCOMPLETE, PASSED, AiScreen, dataset_hash, extraction_eval, screening_eval
from app.extraction_schema import ExtractionSchema, SchemaError, load_schema, schema_version
from app.models import ScreeningCriterion, Source
from app.prescreen import PrescreenError, load_prescreen_prompt, prescreen_source
from app.prompt_registry import load_prompt
from app.untrusted_text import clean_untrusted

HERE = Path(__file__).parent
GOLD_DIR = HERE.parent / "tests" / "gold"
DEFAULT_CRITERIA = HERE / "screening_criteria.json"
REPORTS = HERE / "reports"


def load_gold(path: Path | None = None) -> dict:
    sys.path.insert(0, str(GOLD_DIR.parent))
    from gold.loader import load_gold_set  # validated against the X.15 schema

    return load_gold_set(path)


def load_criteria(path: Path = DEFAULT_CRITERIA) -> list[dict[str, str]]:
    """The criteria the scholar screened the gold set with: `[{code, kind, text}]`."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not all(
        isinstance(c, dict) and {"code", "kind", "text"} <= set(c) and c["kind"] in ("include", "exclude") for c in data
    ):
        raise ValueError(f"{path}: expected a list of {{code, kind: include|exclude, text}}")
    return data


def screening_dataset_hash(gold: dict, criteria: list[dict[str, str]]) -> str:
    papers = [
        {k: p.get(k) for k in ("id", "label", "title", "abstract")}
        for p in sorted(gold["papers"], key=lambda p: p["id"])
    ]
    return dataset_hash("screening", papers, sorted(criteria, key=lambda c: c["code"]))


def run_screening(
    gold: dict,
    criteria: list[dict[str, str]],
    llm: OpenAICompatibleLLM,
    *,
    min_confidence: float,
    prescreen: Callable[..., Any] = prescreen_source,
) -> dict[str, Any]:
    """Pre-screen every gold paper with the real prompt and score it. An error is recorded, never filled in."""
    prompt = load_prescreen_prompt()
    rows = [ScreeningCriterion(code=c["code"], kind=c["kind"], text=c["text"]) for c in criteria]
    answers: dict[str, AiScreen] = {}
    model_ids: set[str] = set()
    for paper in gold["papers"]:
        source = Source(title=paper["title"], abstract=paper.get("abstract"))
        try:
            suggestion = prescreen(llm, rows, source, min_confidence=min_confidence, prompt=prompt)
        except LLMConfigurationError:
            raise  # nothing can be evaluated without a model; stop instead of scoring 100% failures
        except (PrescreenError, LLMResponseError) as exc:
            answers[paper["id"]] = AiScreen(error=str(exc))
            continue
        answers[paper["id"]] = AiScreen(decision=suggestion.decision)
        if suggestion.model_id:
            model_ids.add(suggestion.model_id)

    result = screening_eval({p["id"]: p["label"] for p in gold["papers"]}, answers)
    return {
        "kind": "screening",
        "prompt": prompt.ref,
        "model_id": ", ".join(sorted(model_ids)) or None,
        "min_confidence": min_confidence,
        "dataset_sha256": screening_dataset_hash(gold, criteria),
        "gold_supplied_by": gold.get("supplied_by"),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "result": result.as_dict(),
        # Ids and outcomes only: no titles/abstracts, so the report can be committed and shared.
        "items": [
            {"id": p["id"], "gold": p["label"], "ai": answers[p["id"]].decision, "error": answers[p["id"]].error}
            for p in gold["papers"]
        ],
    }


def extraction_gold(gold: dict) -> dict[str, dict[str, Any]]:
    """`paper id -> field -> gold value`, for the papers the scholar verified an extraction for."""
    return {
        paper["id"]: {field: item["value"] for field, item in paper["extraction"].items()}
        for paper in gold["papers"]
        if paper.get("extraction")
    }


def extraction_dataset_hash(gold: dict, schema: ExtractionSchema) -> str:
    papers = [
        {k: p.get(k) for k in ("id", "title", "abstract", "extraction")}
        for p in sorted(gold["papers"], key=lambda p: p["id"])
        if p.get("extraction")
    ]
    fields = [(f.key, f.type, f.critical) for f in sorted(schema.fields, key=lambda f: f.key)]
    return dataset_hash("extraction", papers, schema_version(schema), fields)


def extraction_hash_for(gold: dict) -> Callable[[str], str | None]:
    """For `baselines.problems`: an extraction report's `schema` (`name@version`) -> the current dataset hash,
    or None when the app no longer has that schema version."""
    def current(schema_ref: str) -> str | None:
        try:
            schema = load_schema(schema_ref.split("@")[0])
        except SchemaError:
            return None
        return extraction_dataset_hash(gold, schema) if schema_version(schema) == schema_ref else None
    return current


def run_extraction(
    gold: dict,
    schema: ExtractionSchema,
    llm: OpenAICompatibleLLM,
    *,
    extract: Callable[..., Any] = extract_with_llm,
) -> dict[str, Any]:
    """Run the real extraction prompt over every gold paper that has a verified extraction, then score it.

    The gold set holds titles and abstracts, so the model reads the abstract. A failed or malformed
    answer is recorded for that paper and never filled in. A missing model stops the run.
    """
    prompt = load_prompt(*AI_EXTRACTION_PROMPT)
    golden = extraction_gold(gold)
    critical = [f.key for f in schema.fields if f.critical]
    answers: dict[str, dict[str, Any] | None] = {}
    errors: dict[str, str] = {}
    model_ids: set[str] = set()
    for paper in gold["papers"]:
        if paper["id"] not in golden:
            continue
        try:
            cleaned = clean_untrusted(paper.get("abstract") or "", max_chars=MAX_PROMPT_TEXT_CHARS)
            if not cleaned.text.strip() or cleaned.truncated:
                raise AiExtractionError("the gold abstract is missing or longer than the prompt limit")
            fields, _evidence, _confidence, model_id, _flags = extract(
                llm, schema=schema, title=paper["title"], text=cleaned.text
            )
        # A malformed reply (missing key, bad number) is this paper's failure, not a reason to lose the whole run.
        except (AiExtractionError, SchemaError, LLMResponseError, KeyError, TypeError, ValueError) as exc:
            answers[paper["id"]] = None
            errors[paper["id"]] = str(exc)
            continue
        answers[paper["id"]] = fields
        model_ids.add(model_id)

    result = extraction_eval(golden, answers, critical)
    return {
        "kind": "extraction",
        "prompt": prompt.ref,
        "model_id": ", ".join(sorted(model_ids)) or None,
        "schema": schema_version(schema),
        "dataset_sha256": extraction_dataset_hash(gold, schema),
        "gold_supplied_by": gold.get("supplied_by"),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "result": result.as_dict(),
        # Ids and outcomes only: no titles, abstracts or extracted values, so the report can be shared.
        "items": [
            {"id": paper_id, "scored_fields": sorted(golden[paper_id]), "error": errors.get(paper_id)}
            for paper_id in golden
        ],
    }


def report_path(report: dict[str, Any]) -> Path:
    model = re.sub(r"[^A-Za-z0-9._-]+", "-", report["model_id"] or "no-model")
    stamp = report["created_at"].replace(":", "").replace("-", "")[:15]
    return REPORTS / f"{report['kind']}__{report['prompt']}__{model}__{stamp}.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("kind", choices=["screening", "extraction"])
    parser.add_argument("--gold", type=Path, default=None)
    parser.add_argument("--criteria", type=Path, default=DEFAULT_CRITERIA)
    parser.add_argument("--schema", default="default", help="extraction schema to run (extraction only)")
    args = parser.parse_args(argv)

    gold = load_gold(args.gold)
    settings = get_settings()
    if args.kind == "extraction":
        if not extraction_gold(gold):
            print("The gold set has no verified extractions (plan X.15 / Q10); there is nothing to evaluate.",
                  file=sys.stderr)
            return 2
        report = run_extraction(gold, load_schema(args.schema), OpenAICompatibleLLM(settings))
    else:
        if not gold["papers"]:
            print("The gold set is empty (plan X.15 / Q10); there is nothing to evaluate.", file=sys.stderr)
            return 2
        report = run_screening(gold, load_criteria(args.criteria), OpenAICompatibleLLM(settings),
                               min_confidence=settings.prescreen_min_confidence)
    path = report_path(report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    status = report["result"]["status"]
    print(f"{status}: {path.relative_to(HERE.parent)}")
    for problem in report["result"]["problems"]:
        print(f"  - {problem}")
    return 0 if status == PASSED else 2 if status == INCOMPLETE else 1


if __name__ == "__main__":
    raise SystemExit(main())
