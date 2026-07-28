from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from mc_han.translator.usage import normalize_usage
from mc_han.usage.models import (
    PricingProfile,
    ReasoningBillingMode,
    TokenUsage,
)
from mc_han.usage.pricing import estimate_cost, select_pricing_profile


def test_normalize_complete_openai_compatible_usage():
    result = normalize_usage(
        {
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 40,
                "total_tokens": 140,
                "prompt_tokens_details": {"cached_tokens": 60},
                "completion_tokens_details": {"reasoning_tokens": 10},
                "cost": "0.0123",
                "currency": "usd",
            }
        }
    )

    assert result.tokens == TokenUsage(
        input_tokens=100,
        output_tokens=40,
        cached_input_tokens=60,
        uncached_input_tokens=40,
        reasoning_tokens=10,
        total_tokens=140,
        reasoning_included_in_output=True,
    )
    assert result.provider_reported_cost == Decimal("0.0123")
    assert result.currency == "USD"
    assert result.diagnostics == ()


def test_normalize_alternate_cache_and_reasoning_fields():
    result = normalize_usage(
        {
            "usage": {
                "input_tokens": 80,
                "output_tokens": 20,
                "cache_hit_tokens": 50,
                "cache_miss_tokens": 30,
                "reasoning_tokens": 8,
            }
        }
    )

    assert result.tokens.cached_input_tokens == 50
    assert result.tokens.uncached_input_tokens == 30
    assert result.tokens.reasoning_tokens == 8
    assert result.tokens.reasoning_included_in_output is None


def test_normalize_uses_stable_cache_and_reasoning_alias_priority():
    result = normalize_usage(
        {
            "usage": {
                "input_tokens": 100,
                "output_tokens": 20,
                "cached_input_tokens": 25,
                "cache_hit_tokens": 50,
                "cached_tokens": 75,
                "prompt_tokens_details": {"cached_tokens": 40},
                "reasoning_tokens": 9,
                "completion_tokens_details": {"reasoning_tokens": 4},
            }
        }
    )

    assert result.tokens.cached_input_tokens == 25
    assert result.tokens.uncached_input_tokens == 75
    assert result.tokens.reasoning_tokens == 4
    assert result.tokens.reasoning_included_in_output is True


def test_invalid_higher_priority_cache_alias_does_not_fall_back():
    result = normalize_usage(
        {
            "usage": {
                "input_tokens": 100,
                "output_tokens": 20,
                "cached_input_tokens": "25",
                "cache_hit_tokens": 50,
            }
        }
    )

    assert result.tokens.cached_input_tokens is None
    assert result.tokens.uncached_input_tokens is None
    assert "usage_invalid_cached_input_tokens" in result.diagnostics


def test_normalize_supports_object_form_usage_details():
    response = SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=60,
            output_tokens=12,
            total_tokens=72,
            prompt_tokens_details=SimpleNamespace(cached_tokens=20),
            completion_tokens_details=SimpleNamespace(reasoning_tokens=3),
        )
    )

    result = normalize_usage(response)

    assert result.tokens.cached_input_tokens == 20
    assert result.tokens.uncached_input_tokens == 40
    assert result.tokens.reasoning_tokens == 3


def test_inconsistent_total_tokens_are_preserved_with_diagnostic():
    result = normalize_usage(
        {
            "usage": {
                "input_tokens": 100,
                "output_tokens": 20,
                "total_tokens": 999,
            }
        }
    )

    assert result.tokens.total_tokens == 999
    assert "total_tokens_inconsistent" in result.diagnostics


def test_missing_usage_remains_unknown():
    result = normalize_usage({"choices": []})

    assert result.tokens == TokenUsage()
    assert result.diagnostics == ("usage_missing",)


def test_inconsistent_cache_breakdown_is_diagnostic_not_failure():
    result = normalize_usage(
        {
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 10,
                "cache_hit_tokens": 30,
                "cache_miss_tokens": 60,
            }
        }
    )

    assert result.tokens.input_tokens == 100
    assert result.tokens.cached_input_tokens is None
    assert result.tokens.uncached_input_tokens is None
    assert "usage_cache_breakdown_mismatch" in result.diagnostics


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("prompt_tokens", True),
        ("prompt_tokens", -1),
        ("prompt_tokens", "100"),
        ("completion_tokens", 1.5),
        ("cached_tokens", False),
        ("reasoning_tokens", -2),
    ],
)
def test_invalid_usage_counts_are_unknown(field: str, value: object):
    usage: dict[str, object] = {
        "prompt_tokens": 10,
        "completion_tokens": 5,
    }
    if field == "cached_tokens":
        usage["prompt_tokens_details"] = {field: value}
    elif field == "reasoning_tokens":
        usage["completion_tokens_details"] = {field: value}
    else:
        usage[field] = value

    result = normalize_usage({"usage": usage})

    diagnostic_field = {
        "prompt_tokens": "input_tokens",
        "completion_tokens": "output_tokens",
        "cached_tokens": "cached_input_tokens",
        "reasoning_tokens": "reasoning_tokens",
    }[field]
    assert f"usage_invalid_{diagnostic_field}" in result.diagnostics


@pytest.mark.parametrize("value", [True, -1, 1.5, "10"])
def test_token_usage_strictly_validates_counts(value: object):
    with pytest.raises((TypeError, ValueError)):
        TokenUsage(input_tokens=value)  # type: ignore[arg-type]


def test_reasoning_tokens_included_in_output_are_not_billed_twice():
    profile = pricing_profile(ReasoningBillingMode.SEPARATE_AS_OUTPUT)
    included = estimate_cost(
        TokenUsage(
            input_tokens=100,
            output_tokens=50,
            cached_input_tokens=0,
            uncached_input_tokens=100,
            reasoning_tokens=20,
            reasoning_included_in_output=True,
        ),
        profile,
    )
    separate = estimate_cost(
        TokenUsage(
            input_tokens=100,
            output_tokens=50,
            cached_input_tokens=0,
            uncached_input_tokens=100,
            reasoning_tokens=20,
            reasoning_included_in_output=False,
        ),
        profile,
    )

    assert included.amount == Decimal("0.0002")
    assert separate.amount == Decimal("0.00024")


def test_no_pricing_profile_is_unknown_not_zero():
    result = estimate_cost(
        TokenUsage(input_tokens=100, output_tokens=20),
        None,
    )

    assert result.amount is None
    assert result.reason == "no_matching_pricing_profile"


def test_unknown_cache_split_refuses_precise_estimate_when_prices_differ():
    result = estimate_cost(
        TokenUsage(input_tokens=100, output_tokens=20),
        pricing_profile(ReasoningBillingMode.INCLUDED_IN_OUTPUT),
    )

    assert result.amount is None
    assert result.reason == "cache_split_unknown"


def test_unknown_cache_split_is_safe_when_input_rates_are_identical():
    profile = pricing_profile(ReasoningBillingMode.INCLUDED_IN_OUTPUT)
    profile = PricingProfile(
        **{
            **profile.to_dict(),
            "input_per_million": Decimal("1"),
            "cached_input_per_million": Decimal("1"),
            "output_per_million": Decimal("2"),
            "reasoning_billing_mode": ReasoningBillingMode.INCLUDED_IN_OUTPUT,
        }
    )

    result = estimate_cost(
        TokenUsage(input_tokens=100, output_tokens=20),
        profile,
    )

    assert result.amount == Decimal("0.00014")


def test_unknown_reasoning_inclusion_keeps_estimate_unknown():
    result = estimate_cost(
        TokenUsage(
            input_tokens=100,
            output_tokens=20,
            reasoning_tokens=5,
            reasoning_included_in_output=None,
        ),
        pricing_profile(ReasoningBillingMode.SEPARATE_AS_OUTPUT),
    )

    assert result.amount is None
    assert result.reason == "reasoning_inclusion_unknown"


def test_pricing_selection_excludes_future_profiles_and_allows_boundary():
    past = pricing_profile(ReasoningBillingMode.INCLUDED_IN_OUTPUT)
    future = PricingProfile(
        **{
            **past.to_dict(),
            "profile_id": "future-profile",
            "effective_from": "2999-01-01T00:00:00+00:00",
            "input_per_million": Decimal("1"),
            "cached_input_per_million": Decimal("0.5"),
            "output_per_million": Decimal("2"),
            "reasoning_billing_mode": ReasoningBillingMode.INCLUDED_IN_OUTPUT,
        }
    )

    selected = select_pricing_profile(
        (future, past),
        provider="fake",
        model="fake-model",
        request_started_at=past.effective_from,
    )

    assert selected == past


def test_pricing_selection_prefers_exact_model_over_pattern():
    pattern = pricing_profile(ReasoningBillingMode.INCLUDED_IN_OUTPUT)
    exact = PricingProfile(
        **{
            **pattern.to_dict(),
            "profile_id": "exact-profile",
            "model_pattern": "fake-model",
            "input_per_million": Decimal("1"),
            "cached_input_per_million": Decimal("0.5"),
            "output_per_million": Decimal("2"),
            "reasoning_billing_mode": ReasoningBillingMode.INCLUDED_IN_OUTPUT,
        }
    )

    selected = select_pricing_profile(
        (pattern, exact),
        provider="fake",
        model="fake-model",
        request_started_at="2026-02-01T00:00:00+00:00",
    )

    assert selected == exact


def test_pricing_selection_rejects_ambiguous_equal_profiles():
    first = pricing_profile(ReasoningBillingMode.INCLUDED_IN_OUTPUT)
    second = PricingProfile(
        **{
            **first.to_dict(),
            "profile_id": "second-profile",
            "input_per_million": Decimal("1"),
            "cached_input_per_million": Decimal("0.5"),
            "output_per_million": Decimal("2"),
            "reasoning_billing_mode": ReasoningBillingMode.INCLUDED_IN_OUTPUT,
        }
    )

    selected = select_pricing_profile(
        (first, second),
        provider="fake",
        model="fake-model",
        request_started_at="2026-02-01T00:00:00+00:00",
    )

    assert selected is None


def pricing_profile(mode: ReasoningBillingMode) -> PricingProfile:
    return PricingProfile(
        profile_id="test-profile-2026-01",
        provider="fake",
        model_pattern="fake-*",
        effective_from="2026-01-01T00:00:00+00:00",
        currency="USD",
        input_per_million=Decimal("1"),
        cached_input_per_million=Decimal("0.5"),
        output_per_million=Decimal("2"),
        reasoning_billing_mode=mode,
        source_reference="test-only pricing profile",
    )
