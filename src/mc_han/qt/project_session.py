from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from mc_han.qt.view_models import WorkflowStage


class TaskKind(str, Enum):
    INSPECTION = "inspection"
    SCAN = "scan"
    TRIAL_TRANSLATION = "trial_translation"
    FULL_TRANSLATION = "full_translation"
    BUILD = "build"
    EXPORT = "export"
    INSTALL = "install"
    ROLLBACK = "rollback"


class CloseDecision(str, Enum):
    WAIT_IN_BACKGROUND = "wait_in_background"
    CANCEL_TASK = "cancel_task"
    ABANDON_CLOSE = "abandon_close"


@dataclass
class ActiveTask:
    kind: TaskKind
    label: str
    worker: Any
    progress: str = "正在启动"
    cancel: Callable[[], None] | None = None

    @property
    def cancellable(self) -> bool:
        return self.cancel is not None


@dataclass
class ProjectSession:
    project_path: Path | None = None
    project_name: str = "未选择项目"
    stage: WorkflowStage = WorkflowStage.WELCOME
    current_page: str = "home"
    settings_return_page: str | None = None
    active_task: ActiveTask | None = None


class WorkflowController(QObject):
    """Own the current project location and the single active background task."""

    changed = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.session = ProjectSession()

    @property
    def task_running(self) -> bool:
        return self.session.active_task is not None

    def set_project(self, path: Path | None, name: str) -> None:
        self.session.project_path = Path(path) if path is not None else None
        self.session.project_name = name or "未选择项目"
        self._emit_changed()

    def set_stage(self, stage: WorkflowStage) -> None:
        if not isinstance(stage, WorkflowStage):
            raise TypeError("stage must be WorkflowStage")
        self.session.stage = stage
        self._emit_changed()

    def set_page(self, page: str) -> None:
        if not isinstance(page, str) or not page:
            raise ValueError("page must not be empty")
        self.session.current_page = page
        self._emit_changed()

    def open_settings(self) -> None:
        if self.session.current_page != "settings":
            self.session.settings_return_page = self.session.current_page
        self.session.current_page = "settings"
        self._emit_changed()

    def update_settings_return_page(self, page: str) -> None:
        if self.session.current_page == "settings":
            self.session.settings_return_page = page
            self._emit_changed()

    def close_settings(self) -> str:
        page = self.session.settings_return_page or "home"
        self.session.settings_return_page = None
        self.session.current_page = page
        self._emit_changed()
        return page

    def begin_task(
        self,
        kind: TaskKind,
        label: str,
        worker: Any,
        *,
        cancel: Callable[[], None] | None = None,
    ) -> tuple[bool, str]:
        if self.session.active_task is not None:
            return (
                False,
                f"{self.session.active_task.label}正在运行，请等待当前任务结束。",
            )
        self.session.active_task = ActiveTask(
            kind=kind,
            label=label,
            worker=worker,
            cancel=cancel,
        )
        self._emit_changed()
        return True, ""

    def update_task_progress(self, progress: str) -> None:
        task = self.session.active_task
        if task is None:
            return
        task.progress = progress or "处理中"
        self._emit_changed()

    def finish_task(self, worker: Any | None = None) -> bool:
        task = self.session.active_task
        if task is None or (worker is not None and task.worker is not worker):
            return False
        self.session.active_task = None
        self._emit_changed()
        return True

    def cancel_active_task(self) -> bool:
        task = self.session.active_task
        if task is None or task.cancel is None:
            return False
        task.cancel()
        task.progress = "已请求取消，正在安全保存"
        self._emit_changed()
        return True

    def _emit_changed(self) -> None:
        self.changed.emit(self.session)
