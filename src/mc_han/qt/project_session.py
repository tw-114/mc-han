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
    pause: Callable[[], None] | None = None
    resume: Callable[[], None] | None = None
    paused: bool = False

    @property
    def cancellable(self) -> bool:
        return self.cancel is not None

    @property
    def pausable(self) -> bool:
        return self.pause is not None and self.resume is not None


@dataclass
class ProjectSession:
    project_path: Path | None = None
    project_name: str = "未选择项目"
    stage: WorkflowStage = WorkflowStage.WELCOME
    current_page: str = "home"
    settings_return_page: str | None = None
    active_task: ActiveTask | None = None


class TaskManager(QObject):
    """Own the single active task and its user-controlled lifecycle."""

    changed = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._active_task: ActiveTask | None = None

    @property
    def active_task(self) -> ActiveTask | None:
        return self._active_task

    @property
    def task_running(self) -> bool:
        return self._active_task is not None

    def begin(
        self,
        kind: TaskKind,
        label: str,
        worker: Any,
        *,
        cancel: Callable[[], None] | None = None,
        pause: Callable[[], None] | None = None,
        resume: Callable[[], None] | None = None,
    ) -> tuple[bool, str]:
        if self._active_task is not None:
            return (
                False,
                f"{self._active_task.label}正在运行，请等待当前任务结束。",
            )
        if (pause is None) is not (resume is None):
            raise ValueError("pause and resume must be provided together")
        self._active_task = ActiveTask(
            kind=kind,
            label=label,
            worker=worker,
            cancel=cancel,
            pause=pause,
            resume=resume,
        )
        self.changed.emit(self._active_task)
        return True, ""

    def update_progress(self, progress: str) -> None:
        task = self._active_task
        if task is None:
            return
        task.progress = progress or "处理中"
        self.changed.emit(task)

    def finish(self, worker: Any | None = None) -> bool:
        task = self._active_task
        if task is None or (worker is not None and task.worker is not worker):
            return False
        self._active_task = None
        self.changed.emit(None)
        return True

    def pause(self) -> bool:
        task = self._active_task
        if task is None or not task.pausable or task.paused:
            return False
        assert task.pause is not None
        task.pause()
        task.paused = True
        task.progress = "将在当前请求完成后暂停"
        self.changed.emit(task)
        return True

    def resume(self) -> bool:
        task = self._active_task
        if task is None or not task.pausable or not task.paused:
            return False
        assert task.resume is not None
        task.resume()
        task.paused = False
        task.progress = "正在继续"
        self.changed.emit(task)
        return True

    def cancel(self) -> bool:
        task = self._active_task
        if task is None or task.cancel is None:
            return False
        task.cancel()
        task.progress = "已请求取消，正在安全保存"
        self.changed.emit(task)
        return True


class WorkflowController(QObject):
    """Own navigation state and expose the shared task manager."""

    changed = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.session = ProjectSession()
        self.task_manager = TaskManager(self)
        self.task_manager.changed.connect(self._task_changed)

    @property
    def task_running(self) -> bool:
        return self.task_manager.task_running

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
        pause: Callable[[], None] | None = None,
        resume: Callable[[], None] | None = None,
    ) -> tuple[bool, str]:
        return self.task_manager.begin(
            kind,
            label,
            worker,
            cancel=cancel,
            pause=pause,
            resume=resume,
        )

    def update_task_progress(self, progress: str) -> None:
        self.task_manager.update_progress(progress)

    def finish_task(self, worker: Any | None = None) -> bool:
        return self.task_manager.finish(worker)

    def pause_active_task(self) -> bool:
        return self.task_manager.pause()

    def resume_active_task(self) -> bool:
        return self.task_manager.resume()

    def cancel_active_task(self) -> bool:
        return self.task_manager.cancel()

    def _task_changed(self, task: ActiveTask | None) -> None:
        self.session.active_task = task
        self._emit_changed()

    def _emit_changed(self) -> None:
        self.changed.emit(self.session)
