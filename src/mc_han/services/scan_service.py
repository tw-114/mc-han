from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from time import perf_counter

from mc_han.core.project import ensure_project_dirs, project_paths
from mc_han.csv_store import (
    CsvWriteError,
    UnsupportedCsvSchemaError,
    read_extracted_csv,
    write_extracted_csv,
)
from mc_han.models import ExtractedText
from mc_han.scanner import (
    ScanRecords,
    ScannerProgress,
    merge_existing_translations,
    scan_modpack,
    write_scan_report,
)
from mc_han.utils.safe_zip import BAD_ZIP, READ_ERROR, ZipDiagnostic
from mc_han.workflow.models import ExistingChineseResources
from mc_han.workflow.scan_models import (
    CATEGORY_DEFINITIONS,
    SCAN_CATEGORY_ORDER,
    ScanCategoryId,
    ScanCategorySummary,
    ScanClassificationResult,
    ScanDiagnostic,
    ScanProgressEvent,
    ScanProgressStage,
    category_for_record,
)


ProgressCallback = Callable[[ScanProgressEvent], None]

_ZIP_DIAGNOSTIC_MESSAGES = {
    "entry_count_limit": "JAR 条目数量超过安全限制，已停止读取该 JAR。",
    "entry_size_limit": "文本条目过大，已跳过该条目。",
    "jar_total_size_limit": "候选文本累计大小超过安全限制，已停止读取该 JAR。",
    "actual_read_limit": "实际读取字节数超过安全限制，已停止读取该 JAR。",
    "compression_ratio_limit": "文本压缩率异常，已跳过该条目。",
    "encrypted_entry": "加密的 JAR 条目无法安全读取，已跳过。",
    BAD_ZIP: "JAR 已损坏或不是有效的 ZIP 文件。",
    READ_ERROR: "JAR 无法读取，已跳过该来源。",
}


@dataclass(frozen=True)
class ScanServiceError(RuntimeError):
    code: str
    message: str
    detail: str
    retryable: bool = True
    partial_saved: bool = False

    def __str__(self) -> str:
        return self.message


def scan_and_classify(
    modpack_dir: Path,
    *,
    progress: ProgressCallback | None = None,
    existing_chinese: ExistingChineseResources | None = None,
    translate_names: bool = False,
) -> ScanClassificationResult:
    started_at = perf_counter()
    saved = False
    try:
        _emit(progress, ScanProgressStage.PREPARING, "正在准备扫描目录")
        paths = project_paths(modpack_dir)
        ensure_project_dirs(paths)
        existing_records = (
            read_extracted_csv(paths.extracted_csv)
            if paths.extracted_csv.exists()
            else []
        )

        _emit(progress, ScanProgressStage.SCANNING, "正在扫描受支持的文本来源")
        def relay_scan_progress(event: ScannerProgress) -> None:
            phase_messages = {
                "ftbquests": "正在扫描 FTB Quests",
                "filesystem": "正在扫描配置和资源包语言文件",
                "jars": "正在扫描模组 JAR",
                "sorting": "正在整理扫描结果",
            }
            _emit(
                progress,
                ScanProgressStage.SCANNING,
                phase_messages.get(event.phase, "正在扫描受支持的文本来源"),
                current_source=event.current_source,
                discovered_records=event.discovered_records,
                processed_jars=event.processed_jars,
                total_jars=event.total_jars,
            )

        records = scan_modpack(
            paths.modpack_dir,
            translate_names=translate_names,
            progress=relay_scan_progress,
        )
        if not isinstance(records, ScanRecords):
            raise TypeError("scan_modpack returned an invalid result")
        if existing_records:
            records = merge_existing_translations(records, existing_records)
        if not isinstance(records, ScanRecords):
            raise TypeError("merged scan records lost inventory")

        elapsed = perf_counter() - started_at
        _emit(
            progress,
            ScanProgressStage.CLASSIFYING,
            "正在整理扫描分类",
            discovered_records=len(records),
        )
        result = classify_scan_records(
            records,
            existing_chinese=existing_chinese,
            scan_duration=elapsed,
            output_csv=paths.extracted_csv.relative_to(paths.modpack_dir).as_posix(),
            report_path=paths.scan_report.relative_to(paths.modpack_dir).as_posix(),
        )

        _emit(
            progress,
            ScanProgressStage.WRITING,
            "正在保存扫描清单和报告",
            discovered_records=len(records),
        )
        try:
            write_extracted_csv(records, paths.extracted_csv)
        except CsvWriteError as error:
            if error.original_preserved:
                if error.target_previously_existed:
                    message = (
                        "扫描已完成，但新的清单保存失败；原有清单已经保留。"
                    )
                else:
                    message = (
                        "扫描已完成，但新的清单保存失败；未留下不完整清单。"
                    )
            else:
                message = (
                    "扫描已完成，但新的清单保存失败；请先检查现有清单后再重试。"
                )
            raise ScanServiceError(
                code="scan_save_failed",
                message=message,
                detail=f"CsvWriteError:{error.phase}",
                retryable=True,
                partial_saved=False,
            ) from error
        saved = True
        write_scan_report(
            modpack_dir=paths.modpack_dir,
            records=records,
            output_csv=paths.extracted_csv,
            report_path=paths.scan_report,
            elapsed_seconds=elapsed,
        )
        _emit(
            progress,
            ScanProgressStage.COMPLETED,
            "扫描和分类已完成",
            discovered_records=len(records),
        )
        return result
    except ScanServiceError:
        raise
    except UnsupportedCsvSchemaError as error:
        raise ScanServiceError(
            code="unsupported_csv_schema",
            message=(
                "现有扫描清单包含当前版本不支持的状态列，已停止重新扫描；"
                "原有清单没有被修改。"
            ),
            detail=type(error).__name__,
            retryable=False,
            partial_saved=False,
        ) from error
    except (OSError, RuntimeError, ValueError, TypeError) as error:
        raise ScanServiceError(
            code="scan_failed",
            message="扫描未能完成，请检查目录是否仍可访问后重试。",
            detail=type(error).__name__,
            retryable=True,
            partial_saved=saved,
        ) from error


def classify_scan_records(
    records: ScanRecords,
    *,
    existing_chinese: ExistingChineseResources | None = None,
    scan_duration: float = 0.0,
    output_csv: str = ".mc-han/extracted_texts.csv",
    report_path: str = ".mc-han/logs/scan_report.txt",
) -> ScanClassificationResult:
    if not isinstance(records, ScanRecords):
        raise TypeError("records must be ScanRecords")

    records_by_category: dict[ScanCategoryId, list[ExtractedText]] = defaultdict(list)
    for record in records:
        category_id = category_for_record(record)
        records_by_category[category_id].append(record)

    categories: list[ScanCategorySummary] = []
    for category_id in SCAN_CATEGORY_ORDER:
        if category_id in {
            ScanCategoryId.EXISTING_CHINESE,
            ScanCategoryId.PROTECTED_SKIPPED,
            ScanCategoryId.SAFETY_REJECTED,
            ScanCategoryId.UNREADABLE_SOURCES,
        }:
            continue
        categories.append(
            _translatable_summary(category_id, records_by_category[category_id])
        )

    diagnostics = _scan_diagnostics(records.inventory)
    categories.extend(
        _information_summaries(
            records.inventory,
            diagnostics,
            existing_chinese=existing_chinese,
        )
    )
    all_files = {
        (record.container, record.file_path)
        for record in records
    }
    all_sources = {record.container for record in records}
    return ScanClassificationResult(
        categories=tuple(categories),
        diagnostics=diagnostics,
        total_translatable_records=len(records),
        total_file_count=len(all_files),
        total_source_count=len(all_sources),
        scan_duration=scan_duration,
        output_csv=_safe_output_path(output_csv),
        report_path=_safe_output_path(report_path),
    )


def _translatable_summary(
    category_id: ScanCategoryId,
    records: list[ExtractedText],
) -> ScanCategorySummary:
    definition = CATEGORY_DEFINITIONS[category_id]
    files = {
        (record.container, record.file_path)
        for record in records
    }
    sources = {record.container for record in records}
    source_types = {record.source_type for record in records}
    recommended = bool(records) and definition.recommended
    return ScanCategorySummary(
        category_id=category_id,
        title=definition.title,
        description=definition.description,
        translatable=True,
        default_selected=recommended,
        record_count=len(records),
        file_count=len(files),
        source_count=len(sources),
        selected=recommended,
        disabled_reason="" if records else "未发现此类内容",
        source_types=tuple(source_types),
        sources=tuple(sources),
    )


def _information_summaries(
    inventory: dict[str, object],
    diagnostics: tuple[ScanDiagnostic, ...],
    *,
    existing_chinese: ExistingChineseResources | None,
) -> tuple[ScanCategorySummary, ...]:
    chinese_sources: tuple[str, ...] = ()
    chinese_files = 0
    chinese_description = "检测阶段未发现中文资源"
    if existing_chinese is not None:
        chinese_sources = existing_chinese.sources
        chinese_files = existing_chinese.item_count
        if existing_chinese.status.value == "partial":
            chinese_description = "检测到部分 zh_cn 资源，不代表整合包已完整汉化"
        elif existing_chinese.status.value == "unknown":
            chinese_description = "部分 JAR 无法检查，中文资源状态无法完整判断"

    safety_diagnostics = tuple(
        item
        for item in diagnostics
        if item.code not in {BAD_ZIP, READ_ERROR}
    )
    unreadable_diagnostics = tuple(
        item
        for item in diagnostics
        if item.code in {BAD_ZIP, READ_ERROR}
    )
    safety_sources = _diagnostic_sources(safety_diagnostics)
    unreadable_sources = _diagnostic_sources(unreadable_diagnostics)
    safety_locations = {item.location for item in safety_diagnostics if item.location}
    unreadable_locations = {
        item.location for item in unreadable_diagnostics if item.location
    }

    summaries = (
        _information_summary(
            ScanCategoryId.EXISTING_CHINESE,
            description=chinese_description,
            file_count=chinese_files,
            sources=chinese_sources,
            disabled_reason="已有中文资源不进入待翻译条目",
        ),
        _information_summary(
            ScanCategoryId.PROTECTED_SKIPPED,
            description=CATEGORY_DEFINITIONS[
                ScanCategoryId.PROTECTED_SKIPPED
            ].description,
            file_count=0,
            sources=(),
            disabled_reason="受保护内容由扫描器自动跳过",
        ),
        _information_summary(
            ScanCategoryId.SAFETY_REJECTED,
            description=(
                f"记录到 {len(safety_diagnostics)} 项安全诊断"
                if safety_diagnostics
                else "未发现因安全限制而拒绝的内容"
            ),
            file_count=len(safety_locations),
            sources=safety_sources,
            disabled_reason="安全诊断仅供检查，不能选择翻译",
        ),
        _information_summary(
            ScanCategoryId.UNREADABLE_SOURCES,
            description=(
                f"有 {len(unreadable_sources)} 个来源损坏或无法读取"
                if unreadable_sources
                else "未发现损坏或无法读取的来源"
            ),
            file_count=len(unreadable_locations),
            sources=unreadable_sources,
            disabled_reason="无法读取的来源没有可翻译记录",
        ),
    )
    return summaries


def _information_summary(
    category_id: ScanCategoryId,
    *,
    description: str,
    file_count: int,
    sources: tuple[str, ...],
    disabled_reason: str,
) -> ScanCategorySummary:
    definition = CATEGORY_DEFINITIONS[category_id]
    return ScanCategorySummary(
        category_id=category_id,
        title=definition.title,
        description=description,
        translatable=False,
        default_selected=False,
        record_count=0,
        file_count=file_count,
        source_count=len(sources),
        selected=False,
        disabled_reason=disabled_reason,
        source_types=(),
        sources=sources,
    )


def _scan_diagnostics(inventory: dict[str, object]) -> tuple[ScanDiagnostic, ...]:
    raw_diagnostics = inventory.get("jar_safety_diagnostics", ())
    diagnostics: list[ScanDiagnostic] = []
    if not isinstance(raw_diagnostics, (list, tuple)):
        return ()
    for item in raw_diagnostics:
        if (
            not isinstance(item, tuple)
            or len(item) != 2
            or not isinstance(item[0], str)
            or not isinstance(item[1], ZipDiagnostic)
        ):
            continue
        container, diagnostic = item
        location = _safe_diagnostic_location(container, diagnostic.entry)
        diagnostics.append(
            ScanDiagnostic(
                severity="error" if diagnostic.code in {BAD_ZIP, READ_ERROR} else "warning",
                code=diagnostic.code,
                message=_ZIP_DIAGNOSTIC_MESSAGES.get(
                    diagnostic.code,
                    "该来源未能通过安全读取检查。",
                ),
                location=location,
            )
        )
    return tuple(diagnostics)


def _diagnostic_sources(
    diagnostics: tuple[ScanDiagnostic, ...],
) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                item.location.split(" :: ", 1)[0]
                for item in diagnostics
                if item.location
            },
            key=lambda value: (value.casefold(), value),
        )
    )


def _safe_diagnostic_location(container: str, entry: str | None) -> str:
    safe_container = _safe_relative_text(container)
    safe_entry = _safe_relative_text(entry or "")
    if safe_container and safe_entry:
        return f"{safe_container} :: {safe_entry}"
    return safe_container


def _safe_relative_text(value: str) -> str:
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
    return value.replace("\\", "/")


def _safe_output_path(value: str) -> str:
    return _safe_relative_text(value) or ""


def _emit(
    callback: ProgressCallback | None,
    stage: ScanProgressStage,
    message: str,
    *,
    current_source: str = "",
    discovered_records: int = 0,
    processed_jars: int = 0,
    total_jars: int = 0,
) -> None:
    if callback is not None:
        callback(
            ScanProgressEvent(
                stage=stage,
                message=message,
                current_source=current_source,
                discovered_records=discovered_records,
                processed_jars=processed_jars,
                total_jars=total_jars,
            )
        )
