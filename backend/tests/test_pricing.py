import pytest
from decimal import Decimal

from app.pricing import call_cost_usd


@pytest.mark.parametrize(
    "model,prompt_tokens,completion_tokens,expected",
    [
        pytest.param(
            "claude-sonnet-5-5",
            1_000_000,
            500_000,
            Decimal("7"),
            id="sonnet-5-5-flat-rate",
        ),  # $2 / MTok input, $10 / MTok output.
        pytest.param(
            "claude-haiku-5-5",
            100_000,
            1_000_000,
            Decimal("0.51"),
            id="haiku-5-5-base-rate-at-100k-prompt-tokens",
        ),  # $0.10 / MTok input, $0.50 / MTok output.
        pytest.param(
            "claude-haiku-5-5",
            200_000,
            100_000,
            Decimal("0.35"),
            id="haiku-5-5-long-prompt-rate-over-100k-prompt-tokens",
        ),  # Over 100,000 prompt tokens: $0.50 / MTok input, $2.50 / MTok output.
    ],
)
def test_call_cost_usd_charges_the_listed_rates_for_the_matching_tier(
    model, prompt_tokens, completion_tokens, expected
):
    assert call_cost_usd(model, prompt_tokens, completion_tokens) == expected


def test_a_model_without_a_price_has_no_cost_rather_than_an_estimate():
    assert call_cost_usd("some-local-model", 1_000, 1_000) is None


def test_a_call_missing_either_token_count_has_no_cost():
    assert call_cost_usd("claude-sonnet-5-5", None, 500) is None
    assert call_cost_usd("claude-sonnet-5-5", 500, None) is None
