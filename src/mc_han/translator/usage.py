from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Mapping

from mc_han.usage.models import TokenUsage, UsageOutcome


@dataclass(frozen=True)
class UsageNormalizationResult:
    tokens: TokenUsage
    diagnostics: tuple[str, ...] = ()
    provider_reported_cost: Decimal | None = None
    currency: str = ""


@dataclass(frozen=True)
class ProviderAttemptResult:
    translations: tuple[str, ...]
    usage: UsageNormalizationResult
    provider_request_id: str = ""
    network_attempt_started: bool = True

    def __post_init__(self) -> None:
        if type(self.network_attempt_started) is not bool:
            raise TypeError("network_attempt_started must be a bool")


class ProviderAttemptError(RuntimeError):
    def __init__(
        self,
        *,
        outcome: UsageOutcome,
        stable_error_code: str,
        retryable: bool,
        usage: UsageNormalizationResult | None = None,
        provider_request_id: str = "",
        network_attempt_started: bool = True,
    ):
        super().__init__(stable_error_code)
        if type(network_attempt_started) is not bool:
            raise TypeError("network_attempt_started must be a bool")
        self.outcome = outcome
        self.stable_error_code = stable_error_code
        self.retryable = retryable
        self.usage = usage or UsageNormalizationResult(TokenUsage())
        self.provider_request_id = provider_request_id
        self.network_attempt_started = network_attempt_started


def normalize_usage(response: object) -> UsageNormalizationResult:
    usage = _field(response, "usage")
    if usage is None:
        return UsageNormalizationResult(TokenUsage(), ("usage_missing",))

    diagnostics: list[str] = []
    input_tokens = _first_count(
        usage,
        ("prompt_tokens", "input_tokens"),
        "input_tokens",
        diagnostics,
    )
    output_tokens = _first_count(
        usage,
        ("completion_tokens", "output_tokens"),
        "output_tokens",
        diagnostics,
    )
    total_tokens = _first_count(
        usage,
        ("total_tokens",),
        "total_tokens",
        diagnostics,
    )
    prompt_details = _field(usage, "prompt_tokens_details")
    completion_details = _field(usage, "completion_tokens_details")
    cached_input_tokens, cached_source_found = _first_count_with_presence(
        usage,
        ("cached_input_tokens",),
        "cached_input_tokens",
        diagnostics,
    )
    if not cached_source_found:
        cached_input_tokens, cached_source_found = _first_count_with_presence(
            prompt_details,
            ("cached_tokens", "cache_hit_tokens"),
            "cached_input_tokens",
            diagnostics,
        )
    if not cached_source_found:
        cached_input_tokens, _cached_source_found = _first_count_with_presence(
            usage,
            (
                "cache_hit_tokens",
                "cached_tokens",
                "prompt_cache_hit_tokens",
            ),
            "cached_input_tokens",
            diagnostics,
        )
    uncached_input_tokens = _first_count(
        usage,
        (
            "cache_miss_tokens",
            "prompt_cache_miss_tokens",
            "uncached_input_tokens",
        ),
        "uncached_input_tokens",
        diagnostics,
    )
    reasoning_tokens = _first_count(
        completion_details,
        ("reasoning_tokens",),
        "reasoning_tokens",
        diagnostics,
    )
    reasoning_included: bool | None = None
    if reasoning_tokens is not None:
        reasoning_included = True
    else:
        reasoning_tokens = _first_count(
            usage,
            ("reasoning_tokens",),
            "reasoning_tokens",
            diagnostics,
        )

    if input_tokens is not None:
        if (
            cached_input_tokens is not None
            and cached_input_tokens <= input_tokens
            and uncached_input_tokens is None
        ):
            uncached_input_tokens = input_tokens - cached_input_tokens
        elif (
            uncached_input_tokens is not None
            and uncached_input_tokens <= input_tokens
            and cached_input_tokens is None
        ):
            cached_input_tokens = input_tokens - uncached_input_tokens
    if (
        cached_input_tokens is not None
        and input_tokens is not None
        and cached_input_tokens > input_tokens
    ):
        diagnostics.append("usage_cached_exceeds_input")
        cached_input_tokens = None
        uncached_input_tokens = None

    if (
        total_tokens is not None
        and input_tokens is not None
        and output_tokens is not None
        and total_tokens != input_tokens + output_tokens
    ):
        diagnostics.append("total_tokens_inconsistent")
    if (
        uncached_input_tokens is not None
        and input_tokens is not None
        and uncached_input_tokens > input_tokens
    ):
        diagnostics.append("usage_uncached_exceeds_input")
        cached_input_tokens = None
        uncached_input_tokens = None
    if (
        cached_input_tokens is not None
        and uncached_input_tokens is not None
        and input_tokens is not None
        and cached_input_tokens + uncached_input_tokens != input_tokens
    ):
        diagnostics.append("usage_cache_breakdown_mismatch")
        cached_input_tokens = None
        uncached_input_tokens = None

    reported_cost = _first_decimal(
        usage,
        ("cost", "reported_cost", "total_cost"),
        "provider_reported_cost",
        diagnostics,
    )
    if reported_cost is None:
        reported_cost = _first_decimal(
            response,
            ("cost", "reported_cost"),
            "provider_reported_cost",
            diagnostics,
        )
    currency = _first_text(usage, ("currency",)) or _first_text(
        response,
        ("currency",),
    )
    if reported_cost is not None and not currency:
        diagnostics.append("reported_cost_currency_missing")
        currency = "UNKNOWN"

    return UsageNormalizationResult(
        tokens=TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            uncached_input_tokens=uncached_input_tokens,
            reasoning_tokens=reasoning_tokens,
            total_tokens=total_tokens,
            reasoning_included_in_output=reasoning_included,
        ),
        diagnostics=tuple(sorted(set(diagnostics))),
        provider_reported_cost=reported_cost,
        currency=currency,
    )


def sanitize_provider_request_id(value: object) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if not value or any(ord(character) < 32 for character in value):
        return ""
    # The shortened digest is only a diagnostic correlation label. It is not an
    # authentication identifier and must never be used for security decisions.
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]
    return f"sha256:{digest}"


def response_request_id(response: object, headers: object = None) -> str:
    header_id = ""
    if headers is not None:
        for name in ("x-request-id", "request-id", "cf-ray"):
            try:
                candidate = headers.get(name)
            except (AttributeError, TypeError):
                candidate = None
            if candidate:
                header_id = str(candidate)
                break
    return sanitize_provider_request_id(header_id or _field(response, "id"))


def _field(value: object, name: str) -> object:
    if value is None:
        return None
    if isinstance(value, Mapping):
        try:
            return value.get(name)
        except (AttributeError, TypeError, RuntimeError):
            return None
    try:
        return getattr(value, name)
    except (AttributeError, TypeError, RuntimeError):
        return None


def _first_count(
    value: object,
    names: tuple[str, ...],
    diagnostic_name: str,
    diagnostics: list[str],
) -> int | None:
    if value is None:
        return None
    for name in names:
        candidate = _field(value, name)
        if candidate is None:
            continue
        if type(candidate) is not int or candidate < 0:
            diagnostics.append(f"usage_invalid_{diagnostic_name}")
            return None
        return candidate
    return None


def _first_count_with_presence(
    value: object,
    names: tuple[str, ...],
    diagnostic_name: str,
    diagnostics: list[str],
) -> tuple[int | None, bool]:
    if value is None:
        return None, False
    for name in names:
        candidate = _field(value, name)
        if candidate is None:
            continue
        if type(candidate) is not int or candidate < 0:
            diagnostics.append(f"usage_invalid_{diagnostic_name}")
            return None, True
        return candidate, True
    return None, False


def _first_decimal(
    value: object,
    names: tuple[str, ...],
    diagnostic_name: str,
    diagnostics: list[str],
) -> Decimal | None:
    for name in names:
        candidate = _field(value, name)
        if candidate is None:
            continue
        if isinstance(candidate, bool) or not isinstance(
            candidate,
            (Decimal, int, float, str),
        ):
            diagnostics.append(f"usage_invalid_{diagnostic_name}")
            return None
        try:
            result = Decimal(str(candidate))
        except (InvalidOperation, ValueError):
            diagnostics.append(f"usage_invalid_{diagnostic_name}")
            return None
        if not result.is_finite() or result < 0:
            diagnostics.append(f"usage_invalid_{diagnostic_name}")
            return None
        return result
    return None


def _first_text(value: object, names: tuple[str, ...]) -> str:
    for name in names:
        candidate = _field(value, name)
        if not isinstance(candidate, str):
            continue
        candidate = candidate.strip().upper()
        if candidate and candidate.isascii() and candidate.isalpha():
            return candidate[:12]
    return ""
