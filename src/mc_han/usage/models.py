from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any

from mc_han.workflow.scan_models import ScanCategoryId

_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+\-]{0,254}$")
_SAFE_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+/\-]{0,254}$")


class UsageOutcome(str, Enum):
    SUCCESS = "success"
    PROVIDER_ERROR = "provider_error"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    CANCELLED = "cancelled"
    INVALID_RESPONSE = "invalid_response"
    LOCAL_VALIDATION_FAILED = "local_validation_failed"


class ReasoningBillingMode(str, Enum):
    INCLUDED_IN_OUTPUT = "included_in_output"
    SEPARATE_AS_OUTPUT = "separate_as_output"
    NOT_BILLED = "not_billed"


def _optional_count(value: int | None, field_name: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an integer or None")
    if value < 0:
        raise ValueError(f"{field_name} must not be negative")
    return value


def _required_count(value: int, field_name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an integer")
    if value < 0:
        raise ValueError(f"{field_name} must not be negative")
    return value


def _optional_decimal(value: Decimal | int | str | None, field_name: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(
        value,
        (Decimal, int, float, str),
    ):
        raise TypeError(f"{field_name} must be a decimal-compatible value or None")
    try:
        decimal_value = Decimal(str(value))
    except Exception as error:
        raise ValueError(f"{field_name} must be a valid decimal") from error
    if not decimal_value.is_finite() or decimal_value < 0:
        raise ValueError(f"{field_name} must be finite and non-negative")
    return decimal_value


def _safe_identifier(
    value: str,
    field_name: str,
    *,
    allow_empty: bool = False,
    allow_slash: bool = False,
) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    value = value.strip()
    if not value and allow_empty:
        return ""
    pattern = _SAFE_MODEL_RE if allow_slash else _SAFE_IDENTIFIER_RE
    if (
        not pattern.fullmatch(value)
        or value.startswith("/")
        or "\\" in value
        or (len(value) >= 3 and value[1:3] in {":/", ":\\"})
        or (allow_slash and any(part in {".", ".."} for part in value.split("/")))
    ):
        raise ValueError(f"{field_name} contains unsupported characters")
    return value


def _stable_strings(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    normalized = tuple(_safe_identifier(value, field_name) for value in tuple(values))
    return tuple(sorted(set(normalized), key=lambda item: (item.casefold(), item)))


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    uncached_input_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None
    reasoning_included_in_output: bool | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "input_tokens",
            "output_tokens",
            "cached_input_tokens",
            "uncached_input_tokens",
            "reasoning_tokens",
            "total_tokens",
        ):
            object.__setattr__(
                self,
                field_name,
                _optional_count(getattr(self, field_name), field_name),
            )
        if self.reasoning_included_in_output is not None and type(
            self.reasoning_included_in_output
        ) is not bool:
            raise TypeError("reasoning_included_in_output must be a bool or None")
        cached = self.cached_input_tokens
        uncached = self.uncached_input_tokens
        input_tokens = self.input_tokens
        if cached is not None and input_tokens is not None and cached > input_tokens:
            raise ValueError("cached_input_tokens must not exceed input_tokens")
        if uncached is not None and input_tokens is not None and uncached > input_tokens:
            raise ValueError("uncached_input_tokens must not exceed input_tokens")
        if cached is not None and uncached is not None and input_tokens is not None:
            if cached + uncached != input_tokens:
                raise ValueError(
                    "cached_input_tokens and uncached_input_tokens must sum to input_tokens"
                )

    @property
    def is_complete(self) -> bool:
        return self.input_tokens is not None and self.output_tokens is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "uncached_input_tokens": self.uncached_input_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "total_tokens": self.total_tokens,
            "reasoning_included_in_output": self.reasoning_included_in_output,
        }


@dataclass(frozen=True)
class UsageCategoryCount:
    category_id: ScanCategoryId
    item_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.category_id, ScanCategoryId):
            raise TypeError("category_id must be ScanCategoryId")
        _required_count(self.item_count, "item_count")
        if self.item_count == 0:
            raise ValueError("item_count must be positive")

    def to_dict(self) -> dict[str, Any]:
        return {
            "category_id": self.category_id.value,
            "item_count": self.item_count,
        }


@dataclass(frozen=True)
class ApiAttemptUsage:
    event_id: str
    task_id: str
    batch_id: str
    attempt_number: int
    provider: str
    model: str
    endpoint_type: str
    thinking_mode: str
    category_items: tuple[UsageCategoryCount, ...]
    source_types: tuple[str, ...]
    item_count: int
    tokens: TokenUsage
    request_started_at: str
    latency_ms: int
    outcome: UsageOutcome
    retryable: bool
    stable_error_code: str
    provider_request_id: str
    provider_reported_cost: Decimal | None = None
    estimated_cost: Decimal | None = None
    currency: str = ""
    pricing_profile_id: str = ""
    usage_diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in (
            "event_id",
            "task_id",
            "batch_id",
            "provider",
            "endpoint_type",
        ):
            object.__setattr__(
                self,
                field_name,
                _safe_identifier(getattr(self, field_name), field_name),
            )
        object.__setattr__(
            self,
            "model",
            _safe_identifier(self.model, "model", allow_slash=True),
        )
        for field_name in (
            "thinking_mode",
            "stable_error_code",
            "provider_request_id",
            "currency",
            "pricing_profile_id",
        ):
            object.__setattr__(
                self,
                field_name,
                _safe_identifier(
                    getattr(self, field_name),
                    field_name,
                    allow_empty=True,
                ),
            )
        if type(self.attempt_number) is not int:
            raise TypeError("attempt_number must be an integer")
        if self.attempt_number <= 0:
            raise ValueError("attempt_number must be positive")
        _required_count(self.item_count, "item_count")
        _required_count(self.latency_ms, "latency_ms")
        if type(self.retryable) is not bool:
            raise TypeError("retryable must be a bool")
        if not isinstance(self.tokens, TokenUsage):
            raise TypeError("tokens must be TokenUsage")
        if not isinstance(self.outcome, UsageOutcome):
            raise TypeError("outcome must be UsageOutcome")
        category_items = tuple(self.category_items)
        if not all(isinstance(item, UsageCategoryCount) for item in category_items):
            raise TypeError("category_items must contain UsageCategoryCount values")
        category_items = tuple(
            sorted(category_items, key=lambda item: item.category_id.value)
        )
        if len({item.category_id for item in category_items}) != len(category_items):
            raise ValueError("category_items must not contain duplicate categories")
        if sum(item.item_count for item in category_items) != self.item_count:
            raise ValueError("category item counts must sum to item_count")
        source_types = _stable_strings(self.source_types, "source_types")
        usage_diagnostics = _stable_strings(
            self.usage_diagnostics,
            "usage_diagnostics",
        )
        if not isinstance(self.request_started_at, str):
            raise TypeError("request_started_at must be a string")
        try:
            datetime.fromisoformat(self.request_started_at.replace("Z", "+00:00"))
        except (TypeError, ValueError) as error:
            raise ValueError("request_started_at must be an ISO-8601 timestamp") from error
        reported = _optional_decimal(
            self.provider_reported_cost,
            "provider_reported_cost",
        )
        estimated = _optional_decimal(self.estimated_cost, "estimated_cost")
        if (reported is not None or estimated is not None) and not self.currency:
            raise ValueError("currency is required when a cost is present")
        if estimated is not None and not self.pricing_profile_id:
            raise ValueError("pricing_profile_id is required for estimated_cost")
        object.__setattr__(self, "category_items", category_items)
        object.__setattr__(self, "source_types", source_types)
        object.__setattr__(self, "usage_diagnostics", usage_diagnostics)
        object.__setattr__(self, "provider_reported_cost", reported)
        object.__setattr__(self, "estimated_cost", estimated)

    @property
    def category_ids(self) -> tuple[ScanCategoryId, ...]:
        return tuple(item.category_id for item in self.category_items)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "task_id": self.task_id,
            "batch_id": self.batch_id,
            "attempt_number": self.attempt_number,
            "provider": self.provider,
            "model": self.model,
            "endpoint_type": self.endpoint_type,
            "thinking_mode": self.thinking_mode,
            "category_items": [item.to_dict() for item in self.category_items],
            "source_types": list(self.source_types),
            "item_count": self.item_count,
            **self.tokens.to_dict(),
            "request_started_at": self.request_started_at,
            "latency_ms": self.latency_ms,
            "outcome": self.outcome.value,
            "retryable": self.retryable,
            "stable_error_code": self.stable_error_code,
            "provider_request_id": self.provider_request_id,
            "provider_reported_cost": (
                str(self.provider_reported_cost)
                if self.provider_reported_cost is not None
                else None
            ),
            "estimated_cost": (
                str(self.estimated_cost)
                if self.estimated_cost is not None
                else None
            ),
            "currency": self.currency,
            "pricing_profile_id": self.pricing_profile_id,
            "usage_diagnostics": list(self.usage_diagnostics),
        }


@dataclass(frozen=True)
class PricingProfile:
    profile_id: str
    provider: str
    model_pattern: str
    effective_from: str
    currency: str
    input_per_million: Decimal
    cached_input_per_million: Decimal
    output_per_million: Decimal
    reasoning_billing_mode: ReasoningBillingMode
    source_reference: str
    is_user_override: bool = False

    def __post_init__(self) -> None:
        for field_name in ("profile_id", "provider", "currency"):
            object.__setattr__(
                self,
                field_name,
                _safe_identifier(getattr(self, field_name), field_name),
            )
        if not isinstance(self.model_pattern, str) or not self.model_pattern.strip():
            raise ValueError("model_pattern must be a non-empty string")
        if not isinstance(self.source_reference, str) or not self.source_reference.strip():
            raise ValueError("source_reference must be a non-empty string")
        try:
            effective_from = datetime.fromisoformat(
                self.effective_from.replace("Z", "+00:00")
            )
        except (TypeError, ValueError) as error:
            raise ValueError("effective_from must be an ISO-8601 timestamp") from error
        if effective_from.tzinfo is None or effective_from.utcoffset() is None:
            raise ValueError("effective_from must include a UTC offset")
        object.__setattr__(
            self,
            "effective_from",
            effective_from.astimezone(timezone.utc).isoformat(),
        )
        for field_name in (
            "input_per_million",
            "cached_input_per_million",
            "output_per_million",
        ):
            value = _optional_decimal(getattr(self, field_name), field_name)
            assert value is not None
            object.__setattr__(self, field_name, value)
        if not isinstance(self.reasoning_billing_mode, ReasoningBillingMode):
            raise TypeError("reasoning_billing_mode must be ReasoningBillingMode")
        if type(self.is_user_override) is not bool:
            raise TypeError("is_user_override must be a bool")

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "provider": self.provider,
            "model_pattern": self.model_pattern,
            "effective_from": self.effective_from,
            "currency": self.currency,
            "input_per_million": str(self.input_per_million),
            "cached_input_per_million": str(
                self.cached_input_per_million
            ),
            "output_per_million": str(self.output_per_million),
            "reasoning_billing_mode": self.reasoning_billing_mode.value,
            "source_reference": self.source_reference,
            "is_user_override": self.is_user_override,
        }


@dataclass(frozen=True)
class CostEstimate:
    amount: Decimal | None
    currency: str
    pricing_profile_id: str
    reason: str

    def __post_init__(self) -> None:
        amount = _optional_decimal(self.amount, "amount")
        for field_name in ("currency", "pricing_profile_id"):
            object.__setattr__(
                self,
                field_name,
                _safe_identifier(
                    getattr(self, field_name),
                    field_name,
                    allow_empty=True,
                ),
            )
        if not isinstance(self.reason, str):
            raise TypeError("reason must be a string")
        if amount is not None and (not self.currency or not self.pricing_profile_id):
            raise ValueError("known cost requires currency and pricing_profile_id")
        object.__setattr__(self, "amount", amount)

    def to_dict(self) -> dict[str, Any]:
        return {
            "amount": str(self.amount) if self.amount is not None else None,
            "currency": self.currency,
            "pricing_profile_id": self.pricing_profile_id,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class TranslationUsageSummary:
    api_attempts: int = 0
    successful_attempts: int = 0
    failed_attempts: int = 0
    translated_items: int = 0
    reused_items: int = 0
    avoided_api_items: int = 0
    local_validation_failed_items: int = 0
    remaining_items: int = 0
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None
    total_latency_ms: int = 0
    average_latency_ms: float | None = None
    p50_latency_ms: float | None = None
    p95_latency_ms: float | None = None
    retry_count: int = 0
    reported_cost_total: Decimal | None = None
    reported_cost_complete: bool = True
    missing_reported_cost_count: int = 0
    reported_cost_currency: str = ""
    estimated_cost: Decimal | None = None
    estimated_cost_currency: str = ""
    unmatched_pricing_count: int = 0
    incomplete_usage_count: int = 0

    def __post_init__(self) -> None:
        for field_name in (
            "api_attempts",
            "successful_attempts",
            "failed_attempts",
            "translated_items",
            "reused_items",
            "avoided_api_items",
            "local_validation_failed_items",
            "remaining_items",
            "total_latency_ms",
            "retry_count",
            "unmatched_pricing_count",
            "incomplete_usage_count",
            "missing_reported_cost_count",
        ):
            _required_count(getattr(self, field_name), field_name)
        for field_name in (
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "total_tokens",
        ):
            _optional_count(getattr(self, field_name), field_name)
        for field_name in (
            "average_latency_ms",
            "p50_latency_ms",
            "p95_latency_ms",
        ):
            value = getattr(self, field_name)
            if value is not None:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise TypeError(f"{field_name} must be numeric or None")
                if not math.isfinite(float(value)) or value < 0:
                    raise ValueError(f"{field_name} must be finite and non-negative")
        reported = _optional_decimal(
            self.reported_cost_total,
            "reported_cost_total",
        )
        estimated = _optional_decimal(self.estimated_cost, "estimated_cost")
        if type(self.reported_cost_complete) is not bool:
            raise TypeError("reported_cost_complete must be a bool")
        if self.reported_cost_complete and self.missing_reported_cost_count:
            raise ValueError(
                "reported_cost_complete conflicts with missing_reported_cost_count"
            )
        for field_name in (
            "reported_cost_currency",
            "estimated_cost_currency",
        ):
            if not isinstance(getattr(self, field_name), str):
                raise TypeError(f"{field_name} must be a string")
        object.__setattr__(self, "reported_cost_total", reported)
        object.__setattr__(self, "estimated_cost", estimated)

    def to_dict(self) -> dict[str, Any]:
        return {
            "api_attempts": self.api_attempts,
            "successful_attempts": self.successful_attempts,
            "failed_attempts": self.failed_attempts,
            "translated_items": self.translated_items,
            "reused_items": self.reused_items,
            "avoided_api_items": self.avoided_api_items,
            "local_validation_failed_items": self.local_validation_failed_items,
            "remaining_items": self.remaining_items,
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "total_tokens": self.total_tokens,
            "total_latency_ms": self.total_latency_ms,
            "average_latency_ms": self.average_latency_ms,
            "p50_latency_ms": self.p50_latency_ms,
            "p95_latency_ms": self.p95_latency_ms,
            "retry_count": self.retry_count,
            "reported_cost": {
                "amount": (
                    str(self.reported_cost_total)
                    if self.reported_cost_total is not None
                    else None
                ),
                "currency": self.reported_cost_currency,
                "complete": self.reported_cost_complete,
                "missing_count": self.missing_reported_cost_count,
            },
            "estimated_cost": (
                str(self.estimated_cost)
                if self.estimated_cost is not None
                else None
            ),
            "estimated_cost_currency": self.estimated_cost_currency,
            "unmatched_pricing_count": self.unmatched_pricing_count,
            "incomplete_usage_count": self.incomplete_usage_count,
        }


@dataclass(frozen=True)
class UsageBreakdown:
    dimension: str
    key: str
    summary: TranslationUsageSummary

    def __post_init__(self) -> None:
        _safe_identifier(self.dimension, "dimension")
        _safe_identifier(
            self.key,
            "key",
            allow_slash=self.dimension == "model",
        )
        if not isinstance(self.summary, TranslationUsageSummary):
            raise TypeError("summary must be TranslationUsageSummary")

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "key": self.key,
            "summary": self.summary.to_dict(),
        }
