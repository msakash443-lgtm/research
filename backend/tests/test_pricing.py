from decimal import Decimal

from app.pricing import call_cost_usd


def test_a_priced_model_costs_its_input_and_output_tokens_at_the_listed_rates():
    # claude-sonnet-5-5: $2 / MTok input, $10 / MTok output.
    assert call_cost_usd("claude-sonnet-5-5", 1_000_000, 500_000) == Decimal("7")


def test_haiku_55_up_to_100k_prompt_tokens_uses_the_base_rates():
    # $0.10 / MTok input, $0.50 / MTok output.
    assert call_cost_usd("claude-haiku-5-5", 100_000, 1_000_000) == Decimal("0.51")


def test_haiku_55_over_100k_prompt_tokens_uses_the_long_prompt_rates_for_that_call():
    # Over 100,000 prompt tokens: $0.50 / MTok input, $2.50 / MTok output.
    assert call_cost_usd("claude-haiku-5-5", 200_000, 100_000) == Decimal("0.35")


def test_a_model_without_a_price_has_no_cost_rather_than_an_estimate():
    assert call_cost_usd("some-local-model", 1_000, 1_000) is None


def test_a_call_missing_either_token_count_has_no_cost():
    assert call_cost_usd("claude-sonnet-5-5", None, 500) is None
    assert call_cost_usd("claude-sonnet-5-5", 500, None) is None
