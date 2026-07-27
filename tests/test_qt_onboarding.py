from __future__ import annotations

import inspect
import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication, QLabel

from mc_han.qt.main_window import MainWindow
from mc_han.qt.task_runner import InspectionTask
from mc_han.qt.view_models import WorkflowStage
from mc_han.version import get_version
from mc_han.workflow.models import (
    CAPABILITY_ORDER,
    ChineseResourceStatus,
    ContentCapability,
    ExistingChineseResources,
    InspectionValidity,
    LoaderInfo,
    ModpackInspection,
)


@pytest.fixture(scope="module")
def application():
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


def make_result(validity: InspectionValidity) -> ModpackInspection:
    return ModpackInspection(
        input_directory=Path("pack"),
        validity=validity,
        display_name="Qt Test Pack",
        minecraft_version="1.20.1",
        loader=LoaderInfo("NeoForge", "47.1.0"),
        mod_count=12,
        capabilities=tuple(
            ContentCapability(key, key, detected=False)
            for key in CAPABILITY_ORDER
        ),
        existing_chinese=ExistingChineseResources(ChineseResourceStatus.NONE),
        messages=(),
        evidence=(),
        inspection_duration=0.2,
    )


def process_until(application: QApplication, predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        if predicate():
            return
        QThread.msleep(5)
    raise AssertionError("Qt condition was not reached before timeout")


def test_window_smoke_and_home_content(application: QApplication):
    window = MainWindow()
    window.show()
    application.processEvents()
    labels = {label.text() for label in window.findChildren(QLabel)}

    assert window.size().width() >= 920
    assert "让 Minecraft 整合包汉化变得简单" in labels
    assert "尚无最近项目" in labels
    assert f"v{get_version()}" in labels
    assert window.home_page.select_button.text() == "选择整合包"

    window.close()
    application.processEvents()


def test_directory_selection_runs_service_off_main_thread(
    application: QApplication,
    tmp_path: Path,
):
    worker_thread_flags: list[bool] = []

    def fake_service(path: Path) -> ModpackInspection:
        worker_thread_flags.append(QThread.currentThread() is application.thread())
        time.sleep(0.03)
        return make_result(InspectionValidity.VALID)

    window = MainWindow(
        inspection_service=fake_service,
        directory_picker=lambda: str(tmp_path),
    )
    window.home_page.select_button.click()

    assert window.stage is WorkflowStage.INSPECTING
    assert not window.inspection_page.reselect_button.isEnabled()
    assert not window.inspection_page.start_scan_button.isEnabled()
    process_until(
        application,
        lambda: window.stage is WorkflowStage.INSPECTION_RESULT,
    )

    assert worker_thread_flags == [False]
    assert window.current_inspection is not None
    assert window.inspection_page.start_scan_button.isEnabled()
    window.close()


@pytest.mark.parametrize(
    ("validity", "enabled"),
    [
        (InspectionValidity.VALID, True),
        (InspectionValidity.PROBABLE, True),
        (InspectionValidity.INVALID, False),
    ],
)
def test_scan_button_follows_validity(
    application: QApplication,
    validity: InspectionValidity,
    enabled: bool,
):
    window = MainWindow()
    window._inspection_completed(make_result(validity))

    assert window.inspection_page.start_scan_button.isEnabled() is enabled
    window.close()
    application.processEvents()


def test_worker_has_no_widget_dependency():
    source = inspect.getsource(InspectionTask)

    assert "QWidget" not in source


def test_failure_restores_window_actions(application: QApplication, tmp_path: Path):
    def failing_service(path: Path) -> ModpackInspection:
        raise RuntimeError("private path must not be shown")

    window = MainWindow(
        inspection_service=failing_service,
        directory_picker=lambda: str(tmp_path),
    )
    window.home_page.select_button.click()
    process_until(
        application,
        lambda: window.footer_status.text() == "检测未完成",
    )

    assert window.inspection_page.reselect_button.isEnabled()
    assert window.inspection_page.home_button.isEnabled()
    assert not window.inspection_page.start_scan_button.isEnabled()
    assert "private path" not in window.inspection_page.status_description.text()
    window.close()
    application.processEvents()
