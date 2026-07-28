from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import PurePosixPath, PureWindowsPath

from mc_han.qt.view_models import StatusTone
from mc_han.workflow.trial_models import (
    TrialSampleResult,
    TrialSampleStatus,
    TrialTranslationResult,
)


@dataclass(frozen=True)
class TrialSampleViewModel:
    text_id: str
    original: str
    translation: str
    category: str
    source: str
    status_text: str
    tone: StatusTone


@dataclass(frozen=True)
class TrialPageViewModel:
    samples: tuple[TrialSampleViewModel, ...]
    successful: str
    failed: str
    tokens: str
    elapsed: str
    cost: str
    can_retry: bool
    can_continue: bool

    @classmethod
    def ready(
        cls,
        samples: tuple[TrialSampleResult, ...],
    ) -> "TrialPageViewModel":
        return cls(
            samples=tuple(_sample_view_model(item) for item in samples),
            successful="0",
            failed="0",
            tokens="请求后显示",
            elapsed="请求后显示",
            cost="请求后显示",
            can_retry=False,
            can_continue=False,
        )

    @classmethod
    def from_result(
        cls,
        result: TrialTranslationResult,
    ) -> "TrialPageViewModel":
        usage = result.usage
        return cls(
            samples=tuple(
                _sample_view_model(item) for item in result.samples
            ),
            successful=str(result.successful_count),
            failed=str(result.failed_count),
            tokens=(
                f"{usage.total_tokens:,}"
                if usage.total_tokens is not None
                else "未知"
            ),
            elapsed=f"{result.elapsed_seconds:.2f} 秒",
            cost=_format_cost(*result.display_cost),
            can_retry=result.failed_count > 0,
            can_continue=result.successful_count > 0,
        )


def _sample_view_model(sample: TrialSampleResult) -> TrialSampleViewModel:
    if sample.status is TrialSampleStatus.SUCCESS:
        status = "缓存复用" if sample.from_cache else "成功"
        tone = StatusTone.SUCCESS
    elif sample.status is TrialSampleStatus.FAILED:
        status = "失败"
        tone = StatusTone.ERROR
    else:
        status = "等待试译"
        tone = StatusTone.NEUTRAL
    return TrialSampleViewModel(
        text_id=sample.text_id,
        original=sample.original,
        translation=sample.translation or "试译后显示",
        category=sample.category_title,
        source=_safe_relative_location(sample.source),
        status_text=status,
        tone=tone,
    )


def _format_cost(
    amount: Decimal | None,
    currency: str,
    source: str,
) -> str:
    if amount is None:
        return source
    unit = currency or "计费单位"
    return f"{amount:.6f} {unit}（{source}）"


def _safe_relative_location(value: str) -> str:
    if not value or "\x00" in value or any(
        not character.isprintable() for character in value
    ):
        return ""
    windows = PureWindowsPath(value)
    posix = PurePosixPath(value)
    if (
        windows.drive
        or windows.root
        or windows.is_absolute()
        or posix.is_absolute()
        or ".." in windows.parts
        or ".." in posix.parts
    ):
        return ""
    return value
