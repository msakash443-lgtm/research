"""Hand-maintained model prices for the observability cost view (X.7.2).

Source: https://platform.claude.com/docs/en/about-claude/pricing (base input and output prices, USD per million tokens),
checked 2026-10-22. Keys are the Claude API model IDs from https://platform.claude.com/docs/en/models/overview.

Only base rates are applied. Cache read/write, batch and other multipliers are not, because the usage record does not
split them. The price is keyed by the configured `LLM_MODEL` string, so it is only correct when that endpoint bills that
model at these rates. Unknown models have no cost (None), never an estimate. Update this table by hand when a price changes.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

_MTOK = Decimal(1_000_000)


@dataclass(frozen=True)
class _Rates:
    input_per_mtok: Decimal
    output_per_mtok: Decimal


@dataclass(frozen=True)
class _Price:
    base: _Rates
    # Some models price a whole call higher when its prompt is longer than `over_tokens`.
    over_tokens: int | None = None
    over: _Rates | None = None


PRICES: dict[str, _Price] = {
    "claude-fable-5-1": _Price(_Rates(Decimal("10"), Decimal("50"))),
    "claude-opus-5-5": _Price(_Rates(Decimal("4"), Decimal("20"))),
    "claude-sonnet-5-5": _Price(_Rates(Decimal("2"), Decimal("10"))),
    "claude-haiku-5-5": _Price(
        _Rates(Decimal("0.10"), Decimal("0.50")),
        over_tokens=100_000,
        over=_Rates(Decimal("0.50"), Decimal("2.50")),
    ),
}


def call_cost_usd(model: str, prompt_tokens: int | None, completion_tokens: int | None) -> Decimal | None:
    """USD cost of one answered call, or None when the model has no price or a token count is missing."""
    price = PRICES.get(model)
    if price is None or prompt_tokens is None or completion_tokens is None:
        return None
    rates = price.base
    if price.over_tokens is not None and price.over is not None and prompt_tokens > price.over_tokens:
        rates = price.over
    return (prompt_tokens * rates.input_per_mtok + completion_tokens * rates.output_per_mtok) / _MTOK
