from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from mc_han.qt.project_session import TaskKind, TaskManager, WorkflowController
from mc_han.qt.view_models import WorkflowStage


def test_project_session_tracks_project_stage_page_and_settings_return(
    tmp_path: Path,
):
    controller = WorkflowController()

    controller.set_project(tmp_path, "Demo Pack")
    controller.set_stage(WorkflowStage.FULL_TRANSLATION)
    controller.set_page("full_translation")
    controller.open_settings()

    assert controller.session.project_path == tmp_path
    assert controller.session.project_name == "Demo Pack"
    assert controller.session.stage is WorkflowStage.FULL_TRANSLATION
    assert controller.session.current_page == "settings"
    assert controller.close_settings() == "full_translation"
    assert controller.session.current_page == "full_translation"


def test_controller_is_single_task_gate_and_supports_safe_cancel():
    controller = WorkflowController()
    first_worker = object()
    second_worker = object()
    cancelled = []

    started, reason = controller.begin_task(
        TaskKind.FULL_TRANSLATION,
        "完整翻译",
        first_worker,
        cancel=lambda: cancelled.append(True),
    )
    duplicate_started, duplicate_reason = controller.begin_task(
        TaskKind.SCAN,
        "扫描整合包",
        second_worker,
    )
    controller.update_task_progress("12/100")

    assert started
    assert reason == ""
    assert not duplicate_started
    assert "完整翻译" in duplicate_reason
    assert controller.session.active_task is not None
    assert controller.session.active_task.progress == "12/100"
    assert not controller.finish_task(second_worker)
    assert controller.cancel_active_task()
    assert cancelled == [True]
    assert controller.finish_task(first_worker)
    assert not controller.task_running


def test_task_manager_owns_pause_resume_progress_and_duplicate_gate():
    manager = TaskManager()
    worker = object()
    calls: list[str] = []

    started, reason = manager.begin(
        TaskKind.FULL_TRANSLATION,
        "完整翻译",
        worker,
        pause=lambda: calls.append("pause"),
        resume=lambda: calls.append("resume"),
    )

    assert started
    assert reason == ""
    assert manager.task_running
    assert manager.pause()
    assert manager.active_task is not None
    assert manager.active_task.paused
    assert manager.active_task.progress == "将在当前请求完成后暂停"
    assert not manager.pause()
    assert manager.resume()
    assert not manager.active_task.paused
    assert calls == ["pause", "resume"]

    duplicate, duplicate_reason = manager.begin(
        TaskKind.SCAN,
        "扫描",
        object(),
    )
    assert not duplicate
    assert "完整翻译" in duplicate_reason
    assert manager.finish(worker)
    assert not manager.task_running


def test_task_manager_requires_pause_and_resume_as_a_pair():
    manager = TaskManager()

    with pytest.raises(ValueError, match="pause and resume"):
        manager.begin(
            TaskKind.FULL_TRANSLATION,
            "完整翻译",
            object(),
            pause=lambda: None,
        )
