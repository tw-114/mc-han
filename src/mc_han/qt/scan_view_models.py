from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath

from mc_han.qt.view_models import StatusTone
from mc_han.workflow.scan_models import (
    ScanCategoryId,
    ScanCategorySummary,
    ScanDiagnostic,
    ScanProgressEvent,
    ScanSelectionState,
)


@dataclass(frozen=True)
class ScanCategoryCardViewModel:
    category_id: ScanCategoryId
    title: str
    description: str
    count_text: str
    selected: bool
    enabled: bool
    disabled_reason: str


@dataclass(frozen=True)
class ScanInformationCardViewModel:
    category_id: ScanCategoryId
    title: str
    description: str
    detail_text: str
    tone: StatusTone


@dataclass(frozen=True)
class ScanDiagnosticViewModel:
    title: str
    message: str
    location: str
    tone: StatusTone


@dataclass(frozen=True)
class ScanPageViewModel:
    project_name: str
    total_records: str
    total_files: str
    total_sources: str
    duration: str
    warnings: str
    selected_summary: str
    can_continue: bool
    categories: tuple[ScanCategoryCardViewModel, ...]
    information: tuple[ScanInformationCardViewModel, ...]
    diagnostics: tuple[ScanDiagnosticViewModel, ...]

    @classmethod
    def from_selection(
        cls,
        project_name: str,
        state: ScanSelectionState,
    ) -> "ScanPageViewModel":
        categories = tuple(
            _category_card(item)
            for item in state.categories
            if item.translatable
        )
        information = tuple(
            _information_card(item)
            for item in state.categories
            if not item.translatable
        )
        selected = state.selected_record_count
        total = state.total_record_count
        return cls(
            project_name=project_name or "未识别",
            total_records=f"{total:,}",
            total_files=f"{state.result.total_file_count:,}",
            total_sources=f"{state.result.total_source_count:,}",
            duration=f"{state.result.scan_duration:.2f} 秒",
            warnings=str(state.result.warning_count),
            selected_summary=(
                f"已选择 {selected:,} 条，共 {total:,} 条可翻译内容"
            ),
            can_continue=selected > 0,
            categories=categories,
            information=information,
            diagnostics=tuple(
                _diagnostic_view_model(item)
                for item in state.result.diagnostics
            ),
        )


@dataclass(frozen=True)
class ScanProgressViewModel:
    stage_text: str
    source_text: str
    discovered_text: str
    jar_progress_text: str
    processed_jars: int
    total_jars: int

    @classmethod
    def from_event(cls, event: ScanProgressEvent) -> "ScanProgressViewModel":
        source = _safe_relative_location(event.current_source)
        return cls(
            stage_text=event.message,
            source_text=f"当前来源：{source}" if source else "",
            discovered_text=f"已发现 {event.discovered_records:,} 条内容",
            jar_progress_text=(
                f"模组进度：{event.processed_jars:,} / {event.total_jars:,}"
                if event.total_jars
                else "模组进度：未发现 JAR"
            ),
            processed_jars=event.processed_jars,
            total_jars=event.total_jars,
        )


def _category_card(
    summary: ScanCategorySummary,
) -> ScanCategoryCardViewModel:
    return ScanCategoryCardViewModel(
        category_id=summary.category_id,
        title=summary.title,
        description=summary.description,
        count_text=(
            f"{summary.record_count:,} 条内容 · "
            f"{summary.file_count:,} 个文件 · "
            f"来自 {summary.source_count:,} 个来源"
        ),
        selected=summary.selected,
        enabled=summary.record_count > 0,
        disabled_reason=summary.disabled_reason,
    )


def _information_card(
    summary: ScanCategorySummary,
) -> ScanInformationCardViewModel:
    if summary.category_id is ScanCategoryId.SAFETY_REJECTED:
        tone = StatusTone.WARNING if summary.file_count else StatusTone.NEUTRAL
        detail = (
            f"涉及 {summary.file_count:,} 个条目 · "
            f"{summary.source_count:,} 个来源"
            if summary.file_count
            else "没有安全拒绝项"
        )
    elif summary.category_id is ScanCategoryId.UNREADABLE_SOURCES:
        tone = StatusTone.ERROR if summary.source_count else StatusTone.SUCCESS
        detail = (
            f"{summary.source_count:,} 个来源需要检查"
            if summary.source_count
            else "所有来源均可读取"
        )
    elif summary.category_id is ScanCategoryId.EXISTING_CHINESE:
        if "无法完整判断" in summary.description:
            tone = StatusTone.WARNING
        elif summary.file_count:
            tone = StatusTone.PRIMARY
        else:
            tone = StatusTone.NEUTRAL
        detail = (
            f"{summary.file_count:,} 个文件 · "
            f"{summary.source_count:,} 个来源"
            if summary.file_count
            else "未计入待翻译内容"
        )
    else:
        tone = StatusTone.NEUTRAL
        detail = "由扫描器自动保护"
    return ScanInformationCardViewModel(
        category_id=summary.category_id,
        title=summary.title,
        description=summary.description,
        detail_text=detail,
        tone=tone,
    )


def _diagnostic_view_model(
    diagnostic: ScanDiagnostic,
) -> ScanDiagnosticViewModel:
    title_by_severity = {
        "error": "来源无法读取",
        "warning": "安全限制已生效",
        "info": "扫描提示",
    }
    tone_by_severity = {
        "error": StatusTone.ERROR,
        "warning": StatusTone.WARNING,
        "info": StatusTone.PRIMARY,
    }
    return ScanDiagnosticViewModel(
        title=title_by_severity[diagnostic.severity],
        message=diagnostic.message,
        location=_safe_relative_location(diagnostic.location),
        tone=tone_by_severity[diagnostic.severity],
    )


def _safe_relative_location(value: str) -> str:
    if not value or "\x00" in value or any(not char.isprintable() for char in value):
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
