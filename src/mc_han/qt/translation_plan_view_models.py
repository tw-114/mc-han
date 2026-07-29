from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from mc_han.qt.view_models import StatusTone
from mc_han.workflow.translation_plan import (
    TranslationPlan,
    TranslationPlanMode,
)


@dataclass(frozen=True)
class TranslationRouteViewModel:
    category: str
    model: str
    record_count: str


@dataclass(frozen=True)
class TranslationPlanPageViewModel:
    mode: TranslationPlanMode
    title: str
    description: str
    recommended: bool
    selected: str
    reused: str
    ai_translation: str
    skipped: str
    requests: str
    tokens: str
    cost: str
    duration: str
    pricing_note: str
    budget_message: str
    budget_tone: StatusTone
    can_continue: bool
    disabled_reason: str
    routes: tuple[TranslationRouteViewModel, ...]

    @classmethod
    def from_plan(
        cls,
        plan: TranslationPlan,
        budget: Decimal | None,
    ) -> "TranslationPlanPageViewModel":
        reused = (
            plan.existing_chinese_reuse_count
            + plan.historical_translation_count
            + plan.cache_reuse_count
        )
        budget_message = "未设置预算上限"
        budget_tone = StatusTone.NEUTRAL
        can_continue = True
        disabled_reason = ""
        if plan.exceeds_budget(budget):
            budget_message = (
                f"预计最高费用已超过 {_money(budget, plan.currency)} 预算，"
                "请调整方案或预算后继续。"
            )
            budget_tone = StatusTone.ERROR
            can_continue = False
            disabled_reason = "预计最高费用超过当前预算上限"
        elif plan.reaches_budget_warning(budget):
            budget_message = (
                f"预计最高费用已达到 {_money(budget, plan.currency)} "
                "预算的 80% 以上。"
            )
            budget_tone = StatusTone.WARNING
        elif budget is not None and budget > 0:
            budget_message = (
                f"预计费用在 {_money(budget, plan.currency)} 预算内。"
            )
            budget_tone = StatusTone.SUCCESS

        return cls(
            mode=plan.mode,
            title=plan.title,
            description=plan.description,
            recommended=plan.recommended,
            selected=f"{plan.selected_record_count:,} 条",
            reused=f"{reused:,} 条",
            ai_translation=f"{plan.ai_translation_count:,} 条",
            skipped=f"{plan.skipped_count:,} 条",
            requests=f"约 {plan.estimated_request_count:,} 次",
            tokens=(
                f"输入约 {plan.estimated_input_tokens:,} · "
                f"输出约 {plan.estimated_output_tokens:,}"
            ),
            cost=_cost_range(plan),
            duration=_duration_range(
                plan.estimated_seconds_low,
                plan.estimated_seconds_high,
            ),
            pricing_note=plan.pricing_note,
            budget_message=budget_message,
            budget_tone=budget_tone,
            can_continue=can_continue,
            disabled_reason=disabled_reason,
            routes=tuple(
                TranslationRouteViewModel(
                    category=route.category_name,
                    model=route.model,
                    record_count=f"{route.record_count:,} 条",
                )
                for route in plan.routes
            ),
        )


def _cost_range(plan: TranslationPlan) -> str:
    if plan.estimated_cost_low is None or plan.estimated_cost_high is None:
        return "费用未知"
    return (
        f"{_money(plan.estimated_cost_low, plan.currency)} - "
        f"{_money(plan.estimated_cost_high, plan.currency)}"
    )


def _money(value: Decimal | None, currency: str) -> str:
    if value is None:
        return "未知"
    unit = "$" if currency == "USD" or not currency else f"{currency} "
    return f"{unit}{value.quantize(Decimal('0.0001'))}"


def _duration_range(low: int, high: int) -> str:
    return f"{_duration(low)} - {_duration(high)}"


def _duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} 秒"
    minutes, remainder = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} 分 {remainder} 秒"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} 小时 {minutes} 分"
