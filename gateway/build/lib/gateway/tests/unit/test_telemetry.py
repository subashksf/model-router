"""Unit tests for cost calculation logic in gateway.telemetry.collector."""

import pytest

from gateway.telemetry.collector import _BASELINE_MODEL, _COST_TABLE, _cost_usd


# ---------------------------------------------------------------------------
# Cost formula
# ---------------------------------------------------------------------------

class TestCostFormula:
    def test_haiku_cost(self):
        rate_in, rate_out = _COST_TABLE["claude-haiku-4-5-20251001"]
        expected = (1000 * rate_in + 500 * rate_out) / 1_000
        assert _cost_usd("claude-haiku-4-5-20251001", 1000, 500) == pytest.approx(expected)

    def test_sonnet_cost(self):
        rate_in, rate_out = _COST_TABLE["claude-sonnet-4-6"]
        expected = (200 * rate_in + 100 * rate_out) / 1_000
        assert _cost_usd("claude-sonnet-4-6", 200, 100) == pytest.approx(expected)

    def test_opus_cost(self):
        rate_in, rate_out = _COST_TABLE["claude-opus-4-6"]
        expected = (500 * rate_in + 250 * rate_out) / 1_000
        assert _cost_usd("claude-opus-4-6", 500, 250) == pytest.approx(expected)

    def test_openai_gpt4o_mini_cost(self):
        rate_in, rate_out = _COST_TABLE["gpt-4o-mini"]
        expected = (1000 * rate_in + 1000 * rate_out) / 1_000
        assert _cost_usd("gpt-4o-mini", 1000, 1000) == pytest.approx(expected)

    def test_gpt4o_baseline_cost(self):
        rate_in, rate_out = _COST_TABLE["gpt-4o"]
        expected = (1000 * rate_in + 500 * rate_out) / 1_000
        assert _cost_usd("gpt-4o", 1000, 500) == pytest.approx(expected)

    def test_zero_tokens_returns_zero(self):
        assert _cost_usd("claude-haiku-4-5-20251001", 0, 0) == 0.0

    def test_zero_input_tokens(self):
        rate_in, rate_out = _COST_TABLE["claude-sonnet-4-6"]
        expected = (0 * rate_in + 100 * rate_out) / 1_000
        assert _cost_usd("claude-sonnet-4-6", 0, 100) == pytest.approx(expected)

    def test_zero_output_tokens(self):
        rate_in, rate_out = _COST_TABLE["claude-haiku-4-5-20251001"]
        expected = (500 * rate_in + 0 * rate_out) / 1_000
        assert _cost_usd("claude-haiku-4-5-20251001", 500, 0) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Unknown model
# ---------------------------------------------------------------------------

class TestUnknownModel:
    def test_unknown_model_returns_zero_cost(self):
        assert _cost_usd("some-future-model-v99", 1000, 500) == 0.0

    def test_empty_model_string_returns_zero_cost(self):
        assert _cost_usd("", 1000, 500) == 0.0


# ---------------------------------------------------------------------------
# Savings calculation
# ---------------------------------------------------------------------------

class TestSavingsCalculation:
    def test_haiku_is_cheaper_than_baseline(self):
        tokens_in, tokens_out = 1000, 500
        actual = _cost_usd("claude-haiku-4-5-20251001", tokens_in, tokens_out)
        baseline = _cost_usd(_BASELINE_MODEL, tokens_in, tokens_out)
        assert actual < baseline

    def test_savings_are_positive_for_cheap_model(self):
        tokens_in, tokens_out = 1000, 500
        actual = _cost_usd("claude-haiku-4-5-20251001", tokens_in, tokens_out)
        baseline = _cost_usd(_BASELINE_MODEL, tokens_in, tokens_out)
        savings = baseline - actual
        assert savings > 0

    def test_savings_math(self):
        tokens_in, tokens_out = 1000, 500
        actual = _cost_usd("claude-haiku-4-5-20251001", tokens_in, tokens_out)
        baseline = _cost_usd("gpt-4o", tokens_in, tokens_out)

        haiku_in, haiku_out = _COST_TABLE["claude-haiku-4-5-20251001"]
        gpt4o_in, gpt4o_out = _COST_TABLE["gpt-4o"]

        expected_actual = (1000 * haiku_in + 500 * haiku_out) / 1_000
        expected_baseline = (1000 * gpt4o_in + 500 * gpt4o_out) / 1_000

        assert actual == pytest.approx(expected_actual)
        assert baseline == pytest.approx(expected_baseline)
        assert (baseline - actual) == pytest.approx(expected_baseline - expected_actual)

    def test_baseline_model_is_gpt4o(self):
        assert _BASELINE_MODEL == "gpt-4o"


# ---------------------------------------------------------------------------
# Cost table completeness
# ---------------------------------------------------------------------------

class TestCostTable:
    def test_all_expected_models_present(self):
        expected = {
            "claude-haiku-4-5-20251001",
            "claude-sonnet-4-6",
            "claude-opus-4-6",
            "gpt-4o-mini",
            "gpt-4o",
        }
        assert expected.issubset(_COST_TABLE.keys())

    def test_all_rates_are_positive(self):
        for model, (rate_in, rate_out) in _COST_TABLE.items():
            assert rate_in > 0, f"{model} has non-positive input rate"
            assert rate_out > 0, f"{model} has non-positive output rate"

    def test_output_rate_higher_than_input_for_all_models(self):
        """Output tokens are always more expensive than input tokens."""
        for model, (rate_in, rate_out) in _COST_TABLE.items():
            assert rate_out > rate_in, f"{model}: output rate should exceed input rate"
