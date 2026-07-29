from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from mc_han.workflow.scan_models import ScanCategoryId


class TranslationPlanMode(str, Enum):
    ECONOMY = "economy"
    BALANCED = "balanced"
    HIGH_QUALITY = "high_quality"


@dataclass(frozen=True)
class TranslationRoute:
    category_id: ScanCategoryId
    category_name: str
    model: str
    record_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.category_id, ScanCategoryId):
            raise TypeError("category_id must be ScanCategoryId")
        if not self.category_name or not self.model:
            raise ValueError("route text fields must not be empty")
        if type(self.record_count) is not int or self.record_count < 0:
            raise ValueError("record_count must be a non-negative integer")


@dataclass(frozen=True)
class TranslationPlan:
    mode: TranslationPlanMode
    title: str
    description: str
    recommended: bool
    selected_record_count: int
    existing_chinese_reuse_count: int
    historical_translation_count: int
    cache_reuse_count: int
    ai_translation_count: int
    skipped_count: int
    estimated_request_count: int
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_cost_low: Decimal | None
    estimated_cost_high: Decimal | None
    currency: str
    estimated_seconds_low: int
    estimated_seconds_high: int
    routes: tuple[TranslationRoute, ...]
    pricing_note: str

    def __post_init__(self) -> None:
        if not isinstance(self.mode, TranslationPlanMode):
            raise TypeError("mode must be TranslationPlanMode")
        for name in (
            "selected_record_count",
            "existing_chinese_reuse_count",
            "historical_translation_count",
            "cache_reuse_count",
            "ai_translation_count",
            "skipped_count",
            "estimated_request_count",
            "estimated_input_tokens",
            "estimated_output_tokens",
            "estimated_seconds_low",
            "estimated_seconds_high",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.estimated_seconds_low > self.estimated_seconds_high:
            raise ValueError("estimated time range is invalid")
        if (
            self.estimated_cost_low is None
        ) is not (self.estimated_cost_high is None):
            raise ValueError("cost range must be complete or unknown")
        if (
            self.estimated_cost_low is not None
            and self.estimated_cost_low > self.estimated_cost_high
        ):
            raise ValueError("estimated cost range is invalid")
        routes = tuple(self.routes)
        if not all(isinstance(route, TranslationRoute) for route in routes):
            raise TypeError("routes must contain TranslationRoute values")
        object.__setattr__(self, "routes", routes)

    def exceeds_budget(self, budget: Decimal | None) -> bool:
        return bool(
            budget is not None
            and budget > 0
            and self.estimated_cost_high is not None
            and self.estimated_cost_high > budget
        )

    def reaches_budget_warning(self, budget: Decimal | None) -> bool:
        return bool(
            budget is not None
            and budget > 0
            and self.estimated_cost_high is not None
            and self.estimated_cost_high >= budget * Decimal("0.8")
        )


@dataclass(frozen=True)
class TranslationPlanComparison:
    plans: tuple[TranslationPlan, ...]
    default_mode: TranslationPlanMode = TranslationPlanMode.BALANCED

    def __post_init__(self) -> None:
        plans = tuple(self.plans)
        if not all(isinstance(plan, TranslationPlan) for plan in plans):
            raise TypeError("plans must contain TranslationPlan values")
        if tuple(plan.mode for plan in plans) != tuple(TranslationPlanMode):
            raise ValueError("plans must follow the stable product order")
        if not isinstance(self.default_mode, TranslationPlanMode):
            raise TypeError("default_mode must be TranslationPlanMode")
        object.__setattr__(self, "plans", plans)

    def for_mode(self, mode: TranslationPlanMode) -> TranslationPlan:
        return next(plan for plan in self.plans if plan.mode is mode)
