from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from mc_han.workflow.models import ModpackInspection


@dataclass(frozen=True)
class TaskFailure:
    code: str
    message: str
    detail: str


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
