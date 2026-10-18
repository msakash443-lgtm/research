"""Qualitative saturation guidance: sample-size ranges by design, with cited sources (M4.3.2).

Pure data and a lookup, standard library only, no AI and no stored state. Only ranges that were checked against a
source are listed here. A design with no verified source (for example ethnography or grounded theory) is refused
rather than given a made-up number (rule 24).

Saturation is a judgement made during analysis, when the data stop yielding new codes or new meaning. These
numbers are planning benchmarks for the protocol and the recruitment budget. They are not a stopping rule.
"""

from __future__ import annotations

from typing import Any

SOURCES: dict[str, str] = {
    "hennink_kaiser_marconi_2017": (
        "Hennink, M., Kaiser, B. N. & Marconi, V. C. (2017). Code saturation versus meaning saturation: how many "
        "interviews are enough? Qualitative Health Research, 27(4), 591-608."
    ),
    "hennink_kaiser_2022": (
        "Hennink, M. & Kaiser, B. N. (2022). Sample sizes for saturation in qualitative research: A systematic review "
        "of empirical tests. Social Science & Medicine, 292, 114523. doi:10.1016/j.socscimed.2021.114523"
    ),
}

CAVEAT = (
    "Saturation is judged during analysis, not fixed in advance. Plan recruitment around these ranges, then record "
    "in the analysis log when and why you judged saturation was reached."
)

DESIGNS: dict[str, dict[str, Any]] = {
    "interviews": {
        "label": "Individual interviews",
        "ranges": [
            {
                "measure": "Code saturation (range of themes identified)",
                "low": 9,
                "high": 9,
                "unit": "interviews",
                "sources": ["hennink_kaiser_marconi_2017"],
            },
            {
                "measure": "Meaning saturation (fuller understanding)",
                "low": 16,
                "high": 24,
                "unit": "interviews",
                "sources": ["hennink_kaiser_marconi_2017"],
            },
            {
                "measure": "Empirical studies overall",
                "low": 9,
                "high": 17,
                "unit": "interviews",
                "sources": ["hennink_kaiser_2022"],
            },
        ],
    },
    "focus_groups": {
        "label": "Focus groups",
        "ranges": [
            {
                "measure": "Empirical studies overall",
                "low": 4,
                "high": 8,
                "unit": "focus groups",
                "sources": ["hennink_kaiser_2022"],
            },
        ],
    },
}

_UPPER_END = (
    "The lower figures in the review came from relatively homogeneous populations with narrowly defined objectives. "
    "For a diverse population or a broad objective, plan for the upper end of the range or beyond."
)


def saturation_guidance(
    design: str, *, homogeneous: bool | None = None, narrow_objective: bool | None = None
) -> dict[str, Any]:
    """Planning ranges for one qualitative design, the sources they come from, and when to plan higher."""
    if design not in DESIGNS:
        raise ValueError(f"design must be one of: {', '.join(DESIGNS)}")
    spec = DESIGNS[design]
    cautions: list[str] = []
    if homogeneous is False or narrow_objective is False:
        cautions.append(_UPPER_END)
    ranges = [dict(r) for r in spec["ranges"]]
    cited = sorted({s for r in ranges for s in r["sources"]})
    return {
        "design": design,
        "label": spec["label"],
        "ranges": ranges,
        "sources": {key: SOURCES[key] for key in cited},
        "cautions": cautions,
        "caveat": CAVEAT,
    }
