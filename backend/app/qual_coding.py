"""Pure qualitative-coding state and inter-coder agreement (M5.5.1).

Works on code names and labels only. It never reads excerpt text, and it does
not suggest codes: suggestions and storage wait on M5.5.2. Every change to the
codebook is recorded in `log`, and no code is removed without a log entry that
names where its excerpts now go.
"""

from collections import Counter


def new_codebook(definitions: dict[str, str]) -> dict:
    """Start a codebook from {code name: definition}. Names must be non-empty."""
    codes = {}
    for name, definition in definitions.items():
        _check_name(name)
        codes[name] = definition
    return {"codes": codes, "merged": {}, "log": []}


def accept(state: dict, name: str, definition: str, actor: str) -> dict:
    """A human accepts a code (for example, an AI suggestion) into the codebook."""
    _check_name(name)
    _check_actor(actor)
    if name in state["codes"] or name in state["merged"]:
        raise ValueError(f"code already exists: {name!r}")
    state["codes"][name] = definition
    state["log"].append({"op": "accept", "code": name, "actor": actor})
    return state


def rename(state: dict, old: str, new: str, actor: str) -> dict:
    """Rename a code. The old name stays resolvable through `resolve`."""
    _check_actor(actor)
    _check_name(new)
    if old not in state["codes"]:
        raise ValueError(f"unknown code: {old!r}")
    if new in state["codes"] or new in state["merged"]:
        raise ValueError(f"code already exists: {new!r}")
    state["codes"][new] = state["codes"].pop(old)
    state["merged"][old] = new
    state["log"].append({"op": "rename", "from": old, "to": new, "actor": actor})
    return state


def merge(state: dict, source: str, target: str, actor: str) -> dict:
    """Merge `source` into `target`. Its definition is kept only on the target."""
    _check_actor(actor)
    if source == target:
        raise ValueError("cannot merge a code into itself")
    if source not in state["codes"]:
        raise ValueError(f"unknown code: {source!r}")
    if target not in state["codes"]:
        raise ValueError(f"unknown code: {target!r}")
    dropped = state["codes"].pop(source)
    state["merged"][source] = target
    # The source's definition leaves the codebook here, so the log keeps it: nothing is dropped silently.
    state["log"].append({"op": "merge", "from": source, "into": target, "definition": dropped, "actor": actor})
    return state


def resolve(state: dict, label: str) -> str:
    """Follow rename and merge history to the code a label now belongs to."""
    seen = set()
    while label in state["merged"]:
        if label in seen:
            raise ValueError(f"cycle in code history at {label!r}")
        seen.add(label)
        label = state["merged"][label]
    if label not in state["codes"]:
        raise ValueError(f"label not in codebook: {label!r}")
    return label


def cohen_kappa(coder_a: list[str], coder_b: list[str]) -> float:
    """Cohen's kappa for two coders' nominal labels over the same excerpts.

    Returns 1.0 when both coders agree everywhere and only one category is used
    (expected agreement is also 1, so kappa is undefined and we report perfect
    agreement rather than dividing by zero).
    """
    if len(coder_a) != len(coder_b):
        raise ValueError("coders must label the same number of excerpts")
    n = len(coder_a)
    if n == 0:
        raise ValueError("no excerpts to compare")
    observed = sum(1 for a, b in zip(coder_a, coder_b) if a == b) / n
    count_a = Counter(coder_a)
    count_b = Counter(coder_b)
    expected = sum(count_a[k] * count_b[k] for k in count_a.keys() | count_b.keys()) / (n * n)
    if expected == 1.0:
        return 1.0
    return (observed - expected) / (1.0 - expected)


def _check_name(name: str) -> None:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("code name must be a non-empty string")


def _check_actor(actor: str) -> None:
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("actor must be named; log entries need a person or agent")
