from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from mc_han.services.scan_service import ScanServiceError
from mc_han.workflow.models import ExistingChineseResources, ModpackInspection
from mc_han.workflow.scan_models import (
    ScanClassificationResult,
    ScanProgressEvent,
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
