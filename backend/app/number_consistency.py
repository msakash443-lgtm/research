"""Statistics quoted in the text must match the stored table values (M7.2.1, spec 5.12 and 5.11 quality checks).

Pure: no database, no model, nothing stored. It reads only the simple form `label = value`, such as `M = 3.42` or
`r = -0.31`. A number is matched when the table value, rounded to the same number of decimals the text uses, equals
the text. Forms it does not read (for example `p < .001`) are not checked and must be checked by a person.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Any


def _rounded(value: float, decimals: int) -> str:
    """The table value rounded half-up (APA style) to `decimals`, as the text would print it. Python's round() is
    half-to-even, so 0.125 would print as 0.12 and reject a correct 0.13. Zero is never printed with a minus sign."""
    rounded = Decimal(repr(value)).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
    return f"{abs(rounded) if rounded == 0 else rounded:f}"


_STATISTIC = re.compile(r"(?<![A-Za-z])([A-Za-z]{1,4}|β|α)\s*=\s*(-?\d+(?:\.(\d+))?)")


def find_mismatches(text: str, table: dict[str, float]) -> dict[str, Any]:
    """`mismatches`: quoted values that differ from the table at the reported precision. `unverified`: quoted labels
    the table has no value for, which are not passed as consistent."""
    mismatches: list[dict[str, Any]] = []
    unverified: list[str] = []
    for match in _STATISTIC.finditer(text):
        label, quoted, decimals_text = match.group(1), match.group(2), match.group(3)
        if label not in table:
            if label not in unverified:
                unverified.append(label)
            continue
        decimals = len(decimals_text or "")
        expected = _rounded(table[label], decimals)
        if quoted != expected:
            mismatches.append({"label": label, "text": quoted, "table": table[label]})
    return {"mismatches": mismatches, "unverified": unverified}
