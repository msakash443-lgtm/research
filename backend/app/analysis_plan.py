"""Analysis plans and generated scripts (M5.3.1-M5.3.2, spec 5.9).

Pure: no database, no model, and nothing is executed here. A plan names the tests, variables and seed for one dataset
version. Its hash covers the plan, the dataset's SHA-256 and the pinned environment, so any change gives a new hash.
The script is generated from the plan, the dataset's column names and its hash; it never reads participant rows.
Running a script on data is a separate step (M5.3.3) that waits on Q4 and a sandbox.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from typing import Any

TESTS = ("descriptives", "correlations")
MAX_VARIABLES = 50
MAX_SEED = 2**31 - 1
ALLOWED_KEYS = {"tests", "variables", "seed"}
# The pinned environment: standard library only, so no lockfile or container image is needed yet.
ENVIRONMENT = {"python": ">=3.11", "packages": [], "note": "standard library only"}


class PlanError(ValueError):
    """A plan that cannot be locked or generated; the message is shown to the researcher."""


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def environment_hash() -> str:
    return sha256_hex(canonical(ENVIRONMENT))


def validate(spec: dict[str, Any], columns: list[str]) -> dict[str, Any]:
    """Return the normalised spec, or raise PlanError. Variables must be columns of the dataset."""
    unknown = set(spec) - ALLOWED_KEYS
    if unknown:
        raise PlanError(f"Unknown plan field(s): {', '.join(sorted(unknown))}.")
    tests = spec.get("tests")
    if not isinstance(tests, list) or not tests:
        raise PlanError("Choose at least one test.")
    if len(set(tests)) != len(tests):
        raise PlanError("Each test may be chosen once.")
    bad_tests = [t for t in tests if t not in TESTS]
    if bad_tests:
        raise PlanError(f"Unknown test(s): {', '.join(map(str, bad_tests))}.")
    variables = spec.get("variables")
    if not isinstance(variables, list) or not variables:
        raise PlanError("Choose at least one variable.")
    if len(variables) > MAX_VARIABLES:
        raise PlanError(f"A plan may use at most {MAX_VARIABLES} variables.")
    if len(set(variables)) != len(variables):
        raise PlanError("Each variable may be chosen once.")
    missing = [v for v in variables if v not in columns]
    if missing:
        raise PlanError(f"Not a column in this dataset: {', '.join(map(str, missing))}.")
    if "correlations" in tests and len(variables) < 2:
        raise PlanError("Correlations need at least two variables.")
    seed = spec.get("seed")
    if not isinstance(seed, int) or isinstance(seed, bool) or not 0 <= seed <= MAX_SEED:
        raise PlanError(f"The seed must be a whole number from 0 to {MAX_SEED}.")
    return {"tests": list(tests), "variables": list(variables), "seed": seed}


def plan_hash(spec: dict[str, Any], dataset_sha256: str) -> str:
    return sha256_hex(canonical({"spec": spec, "dataset_sha256": dataset_sha256, "environment": ENVIRONMENT}))


# The script is a template with five tokens. Values are inserted as Python string literals (repr), never as code.
# No token may sit inside a string or docstring in the template: a repr'd value is only safe as a standalone literal.
_TEMPLATE = '''"""Generated analysis script (M5.3.2). Standard library only; generated, not run by the platform.

Dataset name: see DATASET_NAME below (kept out of this docstring on purpose).
Expected SHA-256 of the CSV: __DATASET_SHA__
Plan hash: __PLAN_HASH__
Environment hash: __ENV_HASH__

Usage: python -I analysis.py <dataset.csv>
Refuses a file whose SHA-256 differs from the one the plan was locked on, and refuses empty or non-numeric cells
rather than dropping them. Prints JSON to stdout. The seed is set before any computation; the current tests use no
randomness, so the output is the same on every run.
"""

import csv
import hashlib
import io
import json
import random
import statistics
import sys

SPEC = json.loads(__SPEC__)
DATASET_NAME = __NAME__
EXPECTED_SHA256 = __DATASET_SHA__
PLAN_HASH = __PLAN_HASH__
ENV_HASH = __ENV_HASH__


def fail(message):
    sys.exit(message)


def load(path):
    with open(path, "rb") as handle:
        raw = handle.read()
    if hashlib.sha256(raw).hexdigest() != EXPECTED_SHA256:
        fail("The dataset does not match the hash the plan was locked on; refusing to run.")
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8"), newline=""))
    columns = {name: [] for name in SPEC["variables"]}
    for line_no, row in enumerate(reader, start=2):
        for name in SPEC["variables"]:
            cell = (row.get(name) or "").strip()
            try:
                value = float(cell)
            except ValueError:
                value = None
            # float() also accepts "nan", "inf", "1e999" (overflows to inf) and "1_0"; none is a usable measurement.
            if value is None or "_" in cell or value != value or value in (float("inf"), float("-inf")):
                fail(f"Row {line_no}, column {name!r}: {cell!r} is not a number. Missing values are not dropped silently.")
            columns[name].append(value)
    return columns


def descriptives(columns):
    out = {}
    for name, values in columns.items():
        out[name] = {
            "n": len(values),
            "mean": statistics.fmean(values),
            "sd": statistics.stdev(values) if len(values) > 1 else None,
            "min": min(values),
            "max": max(values),
        }
    return out


def correlations(columns):
    # A list of pairs, not a dict keyed by "a~b": column names may themselves contain "~", so string keys collide.
    out = []
    names = SPEC["variables"]
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            try:
                out.append({"a": a, "b": b, "r": statistics.correlation(columns[a], columns[b])})
            except statistics.StatisticsError:
                fail(f"Correlation of {a!r} and {b!r} is undefined (a variable has no variation).")
    return out


def main():
    if len(sys.argv) != 2:
        fail("Usage: python -I analysis.py <dataset.csv>")
    random.seed(SPEC["seed"])
    columns = load(sys.argv[1])
    result = {
        "dataset": DATASET_NAME,
        "dataset_sha256": EXPECTED_SHA256,
        "plan_hash": PLAN_HASH,
        "environment_hash": ENV_HASH,
        "seed": SPEC["seed"],
    }
    if "descriptives" in SPEC["tests"]:
        result["descriptives"] = descriptives(columns)
    if "correlations" in SPEC["tests"]:
        result["correlations"] = correlations(columns)
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
'''

ALLOWED_IMPORTS = frozenset({"csv", "hashlib", "io", "json", "random", "statistics", "sys"})


def generate_script(spec: dict[str, Any], dataset_name: str, dataset_sha256: str) -> str:
    """Return the script for a normalised spec. Deterministic: the same inputs always give the same text."""
    values = {
        "__NAME__": repr(dataset_name),
        "__DATASET_SHA__": repr(dataset_sha256),
        "__PLAN_HASH__": repr(plan_hash(spec, dataset_sha256)),
        "__ENV_HASH__": repr(environment_hash()),
        "__SPEC__": repr(canonical(spec)),
    }
    # One pass, so a value that happens to contain a token (e.g. a dataset named "__SPEC__") is never re-substituted.
    return re.sub(r"__[A-Z_]+__", lambda m: values[m.group(0)], _TEMPLATE)


def imported_modules(script: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(script)):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names

