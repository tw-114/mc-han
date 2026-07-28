from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from mc_han.qt.task_runner import FullTranslationTaskResult
from mc_han.translator.engine import TranslationProgress


@dataclass(frozen=True)
class FullTranslationPageViewModel:
    completed_text: str
    total_count: int
    completed_count: int
    current_category: str
    successful_text: str
    failed_text: str
    tokens_text: str
    cost_text: str
    elapsed_text: str
    remaining_count: int
    can_retry: bool
    is_complete: bool

    @classmethod
    def ready(
        cls,
        *,
        total_count: int,
        translated_count: int,
    ) -> "FullTranslationPageViewModel":
        return cls(
            completed_text=f"{translated_count:,} / {total_count:,}",
            total_count=total_count,
            completed_count=translated_count,
            current_category="等待开始",
            successful_text=f"{translated_count:,}",
            failed_text="0",
            tokens_text="请求后显示",
            cost_text="请求后显示",
            elapsed_text="0.00 秒",
            remaining_count=max(0, total_count - translated_count),
            can_retry=False,
            is_complete=translated_count == total_count,
        )

    @classmethod
    def from_progress(
        cls,
        progress: TranslationProgress,
        *,
        total_count: int,
        translated_before_run: int,
        current_category: str,
        elapsed_seconds: float,
    ) -> "FullTranslationPageViewModel":
        completed = min(
            total_count,
            translated_before_run + progress.completed_rows,
        )
        successful = min(
            total_count,
            translated_before_run + progress.translated_rows,
        )
        return cls(
            completed_text=f"{completed:,} / {total_count:,}",
            total_count=total_count,
            completed_count=completed,
            current_category=current_category or "正在准备",
            successful_text=f"{successful:,}",
            failed_text=f"{progress.failed_rows:,}",
            tokens_text="统计中",
            cost_text="统计中",
            elapsed_text=f"{elapsed_seconds:.2f} 秒",
            remaining_count=max(0, total_count - completed),
            can_retry=False,
            is_complete=False,
        )

    @classmethod
    def from_result(
        cls,
        result: FullTranslationTaskResult,
        *,
        current_category: str,
        elapsed_seconds: float,
    ) -> "FullTranslationPageViewModel":
        completed = result.successful_count + len(result.failed_ids)
        usage = result.usage
        return cls(
            completed_text=f"{completed:,} / {result.total_count:,}",
            total_count=result.total_count,
            completed_count=completed,
            current_category=current_category or "已完成",
            successful_text=f"{result.successful_count:,}",
            failed_text=f"{len(result.failed_ids):,}",
            tokens_text=(
                f"{usage.total_tokens:,}"
                if usage.total_tokens is not None
                else "未知"
            ),
            cost_text=_format_cost(result),
            elapsed_text=f"{elapsed_seconds:.2f} 秒",
            remaining_count=result.remaining_count,
            can_retry=bool(result.failed_ids),
            is_complete=(
                result.remaining_count == 0 and not result.failed_ids
            ),
        )


def _format_cost(result: FullTranslationTaskResult) -> str:
    usage = result.usage
    amount: Decimal | None
    currency: str
    source: str
    if (
        usage.reported_cost_complete
        and usage.reported_cost_total is not None
    ):
        amount = usage.reported_cost_total
        currency = usage.reported_cost_currency
        source = "服务商报告"
    elif usage.estimated_cost is not None:
        amount = usage.estimated_cost
        currency = usage.estimated_cost_currency
        source = "估算"
    else:
        return "暂无法确定"
    return f"{amount:.6f} {currency or '计费单位'}（{source}）"
