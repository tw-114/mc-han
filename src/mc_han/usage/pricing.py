from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from fnmatch import fnmatchcase

from .models import (
    CostEstimate,
    PricingProfile,
    ReasoningBillingMode,
    TokenUsage,
)

_MILLION = Decimal(1_000_000)


def select_pricing_profile(
    profiles: tuple[PricingProfile, ...],
    *,
    provider: str,
    model: str,
    request_started_at: str,
) -> PricingProfile | None:
    request_time = _parse_utc_timestamp(
        request_started_at,
        "request_started_at",
    )
    matches = [
        profile
        for profile in profiles
        if profile.provider.casefold() == provider.casefold()
        and fnmatchcase(model.casefold(), profile.model_pattern.casefold())
        and _parse_utc_timestamp(
            profile.effective_from,
            "effective_from",
        )
        <= request_time
    ]
    if not matches:
        return None
    ranked = sorted(
        matches,
        key=lambda profile: _profile_rank(profile, model),
        reverse=True,
    )
    best_rank = _profile_rank(ranked[0], model)
    best = [
        profile
        for profile in ranked
        if _profile_rank(profile, model) == best_rank
    ]
    return best[0] if len(best) == 1 else None


def estimate_cost(
    tokens: TokenUsage,
    profile: PricingProfile | None,
) -> CostEstimate:
    if profile is None:
        return CostEstimate(
            amount=None,
            currency="",
            pricing_profile_id="",
            reason="no_matching_pricing_profile",
        )
    if tokens.input_tokens is None or tokens.output_tokens is None:
        return CostEstimate(
            amount=None,
            currency=profile.currency,
            pricing_profile_id=profile.profile_id,
            reason="incomplete_token_usage",
        )

    billable_output = tokens.output_tokens
    if (
        profile.reasoning_billing_mode is ReasoningBillingMode.SEPARATE_AS_OUTPUT
        and tokens.reasoning_tokens is not None
    ):
        if tokens.reasoning_included_in_output is None:
            return CostEstimate(
                amount=None,
                currency=profile.currency,
                pricing_profile_id=profile.profile_id,
                reason="reasoning_inclusion_unknown",
            )
        if tokens.reasoning_included_in_output is False:
            billable_output += tokens.reasoning_tokens

    cached = tokens.cached_input_tokens
    uncached = tokens.uncached_input_tokens
    if cached is None and uncached is None:
        if profile.cached_input_per_million != profile.input_per_million:
            return CostEstimate(
                amount=None,
                currency=profile.currency,
                pricing_profile_id=profile.profile_id,
                reason="cache_split_unknown",
            )
        cached = 0
        uncached = tokens.input_tokens
    elif cached is None and uncached is not None:
        cached = tokens.input_tokens - uncached
    elif uncached is None and cached is not None:
        uncached = tokens.input_tokens - cached
    assert cached is not None and uncached is not None

    amount = (
        Decimal(uncached) * profile.input_per_million
        + Decimal(cached) * profile.cached_input_per_million
        + Decimal(billable_output) * profile.output_per_million
    ) / _MILLION
    return CostEstimate(
        amount=amount,
        currency=profile.currency,
        pricing_profile_id=profile.profile_id,
        reason="estimated_from_profile",
    )


def _profile_rank(
    profile: PricingProfile,
    model: str,
) -> tuple[int, int, datetime]:
    pattern = profile.model_pattern.casefold()
    normalized_model = model.casefold()
    exact_match = int(pattern == normalized_model)
    specificity = sum(character not in {"*", "?", "[", "]"} for character in pattern)
    return (
        exact_match,
        specificity,
        _parse_utc_timestamp(profile.effective_from, "effective_from"),
    )


def _parse_utc_timestamp(value: str, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a UTC offset")
    return parsed.astimezone(timezone.utc)
