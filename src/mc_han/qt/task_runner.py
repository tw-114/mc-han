from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import sqlite3
from threading import Event
from time import perf_counter

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from mc_han.builder.installer import InstallResult, RollbackResult
from mc_han.core.project import project_paths
from mc_han.csv_store import read_extracted_csv
from mc_han.qt.translation_config_view_models import TranslationSessionConfig
from mc_han.qt.translation_config_view_models import create_translator
from mc_han.services.scan_service import ScanServiceError
from mc_han.services.build_install import (
    BuildWorkflowResult,
    ExportWorkflowResult,
    build_localization_package,
    export_localization_zip,
    install_localization_package,
    rollback_localization_install,
)
from mc_han.services.trial_translation import TrialTranslationError
from mc_han.services.translation_rules import (
    TranslationRuleStore,
    rule_aware_translator,
)
from mc_han.translator.engine import TranslationProgress, translate_csv
from mc_han.usage.ledger import UsageLedger
from mc_han.usage.models import TranslationUsageSummary
from mc_han.usage.service import UsageQueryService
from mc_han.workflow.models import ExistingChineseResources, ModpackInspection
from mc_han.workflow.scan_models import (
    ScanClassificationResult,
    ScanProgressEvent,
)
from mc_han.workflow.trial_models import (
    TrialProgressEvent,
    TrialSampleResult,
    TrialTranslationResult,
)


@dataclass(frozen=True)
class TaskFailure:
    code: str
    message: str
    detail: str
    retryable: bool = True
    partial_saved: bool = False


class InspectionTaskSignals(QObject):
    completed = Signal(object)
    failed = Signal(object)


class InspectionTask(QRunnable):
    def __init__(
        self,
        path: Path,
        inspection_service: Callable[[Path], ModpackInspection],
    ) -> None:
        super().__init__()
        self._path = Path(path)
        self._inspection_service = inspection_service
        self.signals = InspectionTaskSignals()
        self.setAutoDelete(True)

    @Slot()
    def run(self) -> None:
        try:
            inspection = self._inspection_service(self._path)
            if not isinstance(inspection, ModpackInspection):
                raise TypeError("inspection service returned an invalid result")
        except Exception as error:
            self.signals.failed.emit(
                TaskFailure(
                    code="inspection_failed",
                    message="检测过程中发生错误，请重新选择目录后重试。",
                    detail=type(error).__name__,
                )
            )
            return
        self.signals.completed.emit(inspection)


class ScanTaskSignals(QObject):
    progress = Signal(object)
    completed = Signal(object)
    failed = Signal(object)


class ScanTask(QRunnable):
    def __init__(
        self,
        path: Path,
        scan_service: Callable[..., ScanClassificationResult],
        existing_chinese: ExistingChineseResources,
    ) -> None:
        super().__init__()
        self._path = Path(path)
        self._scan_service = scan_service
        self._existing_chinese = existing_chinese
        self.signals = ScanTaskSignals()
        self.setAutoDelete(True)

    @Slot()
    def run(self) -> None:
        try:
            result = self._scan_service(
                self._path,
                progress=self._emit_progress,
                existing_chinese=self._existing_chinese,
            )
            if not isinstance(result, ScanClassificationResult):
                raise TypeError("scan service returned an invalid result")
        except ScanServiceError as error:
            self.signals.failed.emit(
                TaskFailure(
                    code=error.code,
                    message=error.message,
                    detail=error.detail,
                    retryable=error.retryable,
                    partial_saved=error.partial_saved,
                )
            )
            return
        except Exception as error:
            self.signals.failed.emit(
                TaskFailure(
                    code="scan_failed",
                    message="扫描过程中发生错误，请确认目录仍可访问后重试。",
                    detail=type(error).__name__,
                    retryable=True,
                    partial_saved=False,
                )
            )
            return
        self.signals.completed.emit(result)

    def _emit_progress(self, event: ScanProgressEvent) -> None:
        if not isinstance(event, ScanProgressEvent):
            raise TypeError("scan progress callback received an invalid event")
        self.signals.progress.emit(event)


class TrialTaskSignals(QObject):
    progress = Signal(object)
    completed = Signal(object)
    failed = Signal(object)


class TrialTranslationTask(QRunnable):
    def __init__(
        self,
        path: Path,
        config: TranslationSessionConfig,
        samples: tuple[TrialSampleResult, ...],
        trial_service: Callable[..., TrialTranslationResult],
        *,
        task_id: str,
        target_ids: frozenset[str] | None,
    ) -> None:
        super().__init__()
        self._path = Path(path)
        self._config = config
        self._samples = tuple(samples)
        self._trial_service = trial_service
        self._task_id = task_id
        self._target_ids = target_ids
        self.signals = TrialTaskSignals()
        self.setAutoDelete(True)

    @Slot()
    def run(self) -> None:
        try:
            result = self._trial_service(
                self._path,
                self._config,
                self._samples,
                task_id=self._task_id,
                target_ids=self._target_ids,
                progress=self._emit_progress,
            )
            if not isinstance(result, TrialTranslationResult):
                raise TypeError("trial service returned an invalid result")
        except TrialTranslationError as error:
            self.signals.failed.emit(
                TaskFailure(
                    code=error.code,
                    message=error.message,
                    detail=type(error).__name__,
                    retryable=True,
                    partial_saved=True,
                )
            )
            return
        except Exception as error:
            self.signals.failed.emit(
                TaskFailure(
                    code="trial_translation_failed",
                    message=(
                        "试译未能完成，已保存的成功结果仍会保留，"
                        "请稍后重试。"
                    ),
                    detail=type(error).__name__,
                    retryable=True,
                    partial_saved=True,
                )
            )
            return
        self.signals.completed.emit(result)

    def _emit_progress(self, event: TrialProgressEvent) -> None:
        if not isinstance(event, TrialProgressEvent):
            raise TypeError("trial progress callback received an invalid event")
        self.signals.progress.emit(event)


@dataclass(frozen=True)
class FullTranslationTaskResult:
    total_count: int
    successful_count: int
    failed_ids: frozenset[str]
    remaining_ids: frozenset[str]
    usage: TranslationUsageSummary
    elapsed_seconds: float
    task_id: str

    @property
    def remaining_count(self) -> int:
        return len(self.remaining_ids)


class FullTranslationTaskSignals(QObject):
    progress = Signal(object)
    translation_event = Signal(object)
    completed = Signal(object)
    failed = Signal(object)


class FullTranslationTask(QRunnable):
    """Qt adapter around the existing translation engine."""

    def __init__(
        self,
        path: Path,
        config: TranslationSessionConfig,
        *,
        selected_ids: frozenset[str],
        target_ids: frozenset[str],
        task_id: str,
        translator_factory: Callable[[TranslationSessionConfig], object] = (
            create_translator
        ),
    ) -> None:
        super().__init__()
        self._path = Path(path)
        self._config = config
        self._selected_ids = frozenset(selected_ids)
        self._target_ids = frozenset(target_ids)
        self._task_id = task_id
        self._translator_factory = translator_factory
        self._pause_event = Event()
        self._stop_event = Event()
        self.signals = FullTranslationTaskSignals()
        self.setAutoDelete(True)

    def pause(self) -> None:
        self._pause_event.set()

    def resume(self) -> None:
        self._pause_event.clear()

    def request_stop(self) -> None:
        self._stop_event.set()
        self._pause_event.clear()

    @property
    def is_paused(self) -> bool:
        return self._pause_event.is_set()

    @Slot()
    def run(self) -> None:
        paths = project_paths(self._path)
        started = perf_counter()
        try:
            source_records = read_extracted_csv(paths.extracted_csv)
            translator = rule_aware_translator(
                self._translator_factory(self._config),
                source_records,
                TranslationRuleStore(paths.translation_rules_json),
            )
            records, _translated_count, _cache_hits = translate_csv(
                input_csv=paths.extracted_csv,
                output_csv=paths.extracted_csv,
                translator=translator,
                cache_path=paths.translation_cache_jsonl,
                sqlite_cache_path=paths.translations_sqlite,
                usage_ledger_path=paths.usage_sqlite,
                usage_task_id=self._task_id,
                target_ids=set(self._target_ids),
                force_ids={
                    record.id
                    for record in source_records
                    if record.id in self._target_ids
                    and record.review_status == "needs_retranslate"
                },
                worker_count=self._config.concurrency,
                max_batch_items=self._config.batch_size,
                pause_event=self._pause_event,
                stop_event=self._stop_event,
                continue_on_error=True,
                progress_callback=self._emit_progress,
                event_callback=self.signals.translation_event.emit,
            )
        except (OSError, sqlite3.Error, RuntimeError, TypeError, ValueError) as error:
            self.signals.failed.emit(
                TaskFailure(
                    code="full_translation_failed",
                    message=(
                        "完整翻译未能继续，已完成批次已经保存，"
                        "可稍后继续。"
                    ),
                    detail=type(error).__name__,
                    retryable=True,
                    partial_saved=True,
                )
            )
            return

        selected_records = tuple(
            record for record in records if record.id in self._selected_ids
        )
        successful_count = sum(
            bool(record.translation.strip()) for record in selected_records
        )
        failed_ids = frozenset(
            record.id
            for record in selected_records
            if not record.translation.strip()
            and record.note.casefold().startswith("failed")
        )
        remaining_ids = frozenset(
            record.id
            for record in selected_records
            if not record.translation.strip() and record.id not in failed_ids
        )
        try:
            with UsageLedger(paths.usage_sqlite) as ledger:
                usage = UsageQueryService(ledger).task_summary(self._task_id)
        except (OSError, sqlite3.Error, ValueError):
            usage = TranslationUsageSummary()
        self.signals.completed.emit(
            FullTranslationTaskResult(
                total_count=len(self._selected_ids),
                successful_count=successful_count,
                failed_ids=failed_ids,
                remaining_ids=remaining_ids,
                usage=usage,
                elapsed_seconds=perf_counter() - started,
                task_id=self._task_id,
            )
        )

    def _emit_progress(self, progress: TranslationProgress) -> None:
        if not isinstance(progress, TranslationProgress):
            raise TypeError(
                "translation progress callback received an invalid event"
            )
        self.signals.progress.emit(progress)


class BuildInstallTaskSignals(QObject):
    completed = Signal(object)
    failed = Signal(object)


class BuildTask(QRunnable):
    def __init__(
        self,
        *,
        modpack_dir: Path,
        csv_path: Path,
        output_dir: Path,
        minecraft_version: str,
        service: Callable[..., BuildWorkflowResult] = (
            build_localization_package
        ),
    ) -> None:
        super().__init__()
        self._modpack_dir = Path(modpack_dir)
        self._csv_path = Path(csv_path)
        self._output_dir = Path(output_dir)
        self._minecraft_version = minecraft_version
        self._service = service
        self.signals = BuildInstallTaskSignals()
        self.setAutoDelete(True)

    @Slot()
    def run(self) -> None:
        try:
            result = self._service(
                modpack_dir=self._modpack_dir,
                csv_path=self._csv_path,
                output_dir=self._output_dir,
                minecraft_version=self._minecraft_version,
            )
            if not isinstance(result, BuildWorkflowResult):
                raise TypeError("build service returned an invalid result")
        except Exception as error:
            self.signals.failed.emit(
                TaskFailure(
                    code="build_failed",
                    message=(
                        "资源包生成失败。已有成功输出没有被主动删除，"
                        "请检查译文和源文件后重试。"
                    ),
                    detail=type(error).__name__,
                    retryable=True,
                    partial_saved=True,
                )
            )
            return
        self.signals.completed.emit(result)


class ExportTask(QRunnable):
    def __init__(
        self,
        *,
        output_dir: Path,
        service: Callable[..., ExportWorkflowResult] = (
            export_localization_zip
        ),
    ) -> None:
        super().__init__()
        self._output_dir = Path(output_dir)
        self._service = service
        self.signals = BuildInstallTaskSignals()
        self.setAutoDelete(True)

    @Slot()
    def run(self) -> None:
        try:
            result = self._service(output_dir=self._output_dir)
            if not isinstance(result, ExportWorkflowResult):
                raise TypeError("export service returned an invalid result")
        except Exception as error:
            self.signals.failed.emit(
                TaskFailure(
                    code="export_failed",
                    message=(
                        "ZIP 导出失败，已有构建目录仍然保留，"
                        "请确认输出目录可写后重试。"
                    ),
                    detail=type(error).__name__,
                    retryable=True,
                    partial_saved=True,
                )
            )
            return
        self.signals.completed.emit(result)


class InstallTask(QRunnable):
    def __init__(
        self,
        *,
        modpack_dir: Path,
        output_dir: Path,
        service: Callable[..., InstallResult] = (
            install_localization_package
        ),
    ) -> None:
        super().__init__()
        self._modpack_dir = Path(modpack_dir)
        self._output_dir = Path(output_dir)
        self._service = service
        self.signals = BuildInstallTaskSignals()
        self.setAutoDelete(True)

    @Slot()
    def run(self) -> None:
        try:
            result = self._service(
                modpack_dir=self._modpack_dir,
                output_dir=self._output_dir,
            )
            if not isinstance(result, InstallResult):
                raise TypeError("install service returned an invalid result")
        except Exception as error:
            self.signals.failed.emit(
                TaskFailure(
                    code="install_failed",
                    message=(
                        "安装未能完成。请保留输出目录和备份目录，"
                        "检查错误后重试。"
                    ),
                    detail=type(error).__name__,
                    retryable=True,
                    partial_saved=True,
                )
            )
            return
        self.signals.completed.emit(result)


class RollbackTask(QRunnable):
    def __init__(
        self,
        *,
        modpack_dir: Path,
        backup_dir: Path,
        service: Callable[..., RollbackResult] = (
            rollback_localization_install
        ),
    ) -> None:
        super().__init__()
        self._modpack_dir = Path(modpack_dir)
        self._backup_dir = Path(backup_dir)
        self._service = service
        self.signals = BuildInstallTaskSignals()
        self.setAutoDelete(True)

    @Slot()
    def run(self) -> None:
        try:
            result = self._service(
                modpack_dir=self._modpack_dir,
                backup_dir=self._backup_dir,
            )
            if not isinstance(result, RollbackResult):
                raise TypeError("rollback service returned an invalid result")
        except Exception as error:
            self.signals.failed.emit(
                TaskFailure(
                    code="rollback_failed",
                    message=(
                        "撤销安装失败。备份和安装清单仍然保留，"
                        "请不要手动删除它们。"
                    ),
                    detail=type(error).__name__,
                    retryable=True,
                    partial_saved=True,
                )
            )
            return
        self.signals.completed.emit(result)
