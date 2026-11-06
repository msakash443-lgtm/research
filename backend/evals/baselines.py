"""The regression rule for prompts (plan X.5): every prompt version the app uses has a baseline entry, and
once there is gold data, that entry points at a report made from the current gold set. See README.md."""

from __future__ import annotations

import importlib
import json
import re
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).parent
APP_DIR = HERE.parent / "app"
BASELINES = HERE / "baselines.json"

_CALL = re.compile(r"load_prompt\(([^)]*)\)")
_STAR_CONSTANT = re.compile(r"^\*([A-Z][A-Z0-9_]*)$")
EVAL_KINDS = {"screening", "extraction", "none"}


def live_prompt_refs(app_dir: Path = APP_DIR) -> dict[str, str]:
    """`name@version` -> module, for every `load_prompt(*CONSTANT)` call in the app.

    A call in any other form (a literal, a variable) is an error: the rule can only cover what it can see.
    """
    refs: dict[str, str] = {}
    for path in sorted(app_dir.rglob("*.py")):
        if path.name == "prompt_registry.py":
            continue
        for argument in _CALL.findall(path.read_text(encoding="utf-8")):
            match = _STAR_CONSTANT.match(argument.strip())
            if not match:
                raise ValueError(
                    f"{path.relative_to(app_dir.parent)}: call load_prompt(*CONSTANT) with a module-level "
                    f"(name, version) constant so the evaluation rule can find it, not load_prompt({argument})"
                )
            module_name = ".".join(path.relative_to(app_dir.parent).with_suffix("").parts)
            name, version = getattr(importlib.import_module(module_name), match.group(1))
            refs[f"{name}@{version}"] = module_name
    return refs


def problems(
    baselines: dict[str, Any], live: set[str], *, has_gold: bool, current_hash: str, report_dir: Path,
    has_extraction_gold: bool = False, extraction_hash: Callable[[str], str | None] | None = None,
) -> list[str]:
    """Everything wrong with `baselines` for these live prompt refs. Empty means the rule holds.

    `has_gold` says the screening gold set has papers; `has_extraction_gold` says it has verified extractions.
    `current_hash` is the screening dataset hash. An extraction report is hashed with its own schema, so
    `extraction_hash` maps the report's `schema` (`name@version`) to the current hash, or None if the app
    has no such schema.
    """
    found: list[str] = []
    for ref in sorted(live - set(baselines)):
        found.append(f"{ref}: no baseline entry; run the evaluation (or add an eval 'none' entry with a reason)")
    for ref in sorted(set(baselines) - live):
        found.append(f"{ref}: baseline entry for a prompt version the app no longer uses; remove it")
    for ref in sorted(live & set(baselines)):
        entry = baselines[ref]
        kind = entry.get("eval")
        if kind not in EVAL_KINDS:
            found.append(f"{ref}: eval must be one of {sorted(EVAL_KINDS)}")
            continue
        if kind == "none":
            if not str(entry.get("reason", "")).strip():
                found.append(f"{ref}: eval 'none' needs a reason")
            continue
        status = entry.get("status")
        has_data = has_extraction_gold if kind == "extraction" else has_gold
        if not has_data:
            # Without gold data of this kind nothing can be measured yet.
            if status != "unevaluated" or not str(entry.get("reason", "")).strip():
                found.append(f"{ref}: can't be evaluated yet, so status must be 'unevaluated' with a reason")
            continue
        if status not in ("passed", "failed"):
            found.append(f"{ref}: the gold set has data now; run the evaluation (status is {status!r})")
            continue
        if status == "failed" and not (str(entry.get("accepted_by", "")).strip() and str(entry.get("reason", "")).strip()):
            found.append(f"{ref}: a failed baseline ships only with accepted_by and a reason")
        report_name = entry.get("report")
        report_file = report_dir / str(report_name) if report_name else None
        if report_file is None or not report_file.is_file():
            found.append(f"{ref}: report {report_name!r} not found in {report_dir.name}/")
            continue
        report = json.loads(report_file.read_text(encoding="utf-8"))
        if report.get("prompt") != ref or report.get("kind") != kind:
            found.append(f"{ref}: report {report_name} is for {report.get('kind')} {report.get('prompt')}")
        expected_hash = current_hash
        if kind == "extraction":
            expected_hash = extraction_hash(str(report.get("schema"))) if extraction_hash else None
            if expected_hash is None:
                found.append(f"{ref}: report {report_name} names extraction schema {report.get('schema')!r}, "
                             "which the app does not have; re-run it")
        if expected_hash is not None and report.get("dataset_sha256") != expected_hash:
            found.append(f"{ref}: report {report_name} was made from a different gold set or criteria; re-run it")
        if report.get("result", {}).get("status") != status:
            found.append(f"{ref}: baseline says {status!r} but the report says {report.get('result', {}).get('status')!r}")
    return found
