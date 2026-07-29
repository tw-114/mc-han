from __future__ import annotations

import inspect
import os
import time
from dataclasses import replace
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication, QLabel

from mc_han.models import ExtractedText
from mc_han.qt.main_window import MainWindow
from mc_han.qt.task_runner import InspectionTask
from mc_han.qt.view_models import WorkflowStage
from mc_han.scanner import ScanRecords
from mc_han.services.scan_service import classify_scan_records
from mc_han.services.recent_projects import (
    RecentProject,
    RecentProjectsStore,
)
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
    assert "从整合包到可安装汉化包" in labels
    assert any(
        "1. 整合包" in label.text()
        for label in window.findChildren(QLabel)
    )
    assert f"v{get_version()}" in labels
    assert window.home_page.select_button.text() == "选择整合包"
    assert [button.text() for button in window.workflow_step_buttons] == [
        "1. 整合包",
        "2. 汉化",
        "3. 安装",
    ]
    assert window.workflow_step_buttons[0].isChecked()
    assert not window.workflow_step_buttons[1].isEnabled()
    assert "扫描" in window.workflow_step_buttons[1].toolTip()

    window.close()
    application.processEvents()


def test_home_shows_recent_project_and_continues_it(
    application: QApplication,
    tmp_path: Path,
):
    project_path = tmp_path / "Demo"
    project_path.mkdir()
    store = RecentProjectsStore(tmp_path / "projects.json")
    store.upsert(
        RecentProject(
            path=project_path,
            display_name="Demo Pack",
            minecraft_version="1.21.1",
            loader_name="NeoForge",
            last_opened="2026-01-01T00:00:00+00:00",
        )
    )
    inspected: list[Path] = []

    def inspect_recent(path: Path) -> ModpackInspection:
        inspected.append(path)
        return replace(make_result(InspectionValidity.VALID), input_directory=path)

    window = MainWindow(
        recent_projects_store=store,
        project_discovery_service=lambda _paths: (),
        inspection_service=inspect_recent,
    )
    assert window.home_page.continue_button.isVisibleTo(window)
    assert "Demo Pack" in window.home_page.continue_button.text()

    window.home_page.continue_button.click()
    process_until(
        application,
        lambda: window.stage is WorkflowStage.INSPECTION_RESULT,
    )

    assert inspected == [project_path]
    assert store.load().most_recent is not None
    assert store.load().most_recent.last_page == "inspection"
    window.close()
    application.processEvents()


def test_settings_is_always_available_and_returns_to_current_page(
    application: QApplication,
):
    window = MainWindow()
    original_page = window.pages.currentWidget()

    assert window.settings_button.isEnabled()
    window.settings_button.click()
    application.processEvents()
    assert window.pages.currentWidget() is window.settings_page
    assert window.settings_page.back_button.isEnabled()

    window.settings_page.back_button.click()
    application.processEvents()
    assert window.pages.currentWidget() is original_page
    window.close()


def test_completed_workflow_step_can_return(
    application: QApplication,
):
    def fake_scan(*_args, **_kwargs):
        return classify_scan_records(
            ScanRecords(
                [
                    ExtractedText(
                        id="demo",
                        source_type="jar_lang",
                        container="mods/demo.jar",
                        file_path="assets/demo/lang/en_us.json",
                        key_path="demo.text",
                        original="Demo text",
                    )
                ],
                inventory={},
            ),
            scan_duration=0.1,
        )

    window = MainWindow(scan_service=fake_scan)
    inspection = make_result(InspectionValidity.VALID)
    window._inspection_completed(inspection)

    assert window.workflow_step_buttons[0].isChecked()
    window.workflow_step_buttons[0].click()
    process_until(
        application,
        lambda: window.stage is WorkflowStage.SCAN_RESULT,
    )
    assert window.pages.currentWidget() is window.scan_page
    assert window.workflow_step_buttons[0].isChecked()
    assert window.workflow_step_buttons[1].isEnabled()

    window.workflow_step_buttons[1].click()
    application.processEvents()
    assert window.pages.currentWidget() is window.translation_config_page
    assert window.workflow_step_buttons[1].isChecked()

    window.workflow_step_buttons[0].click()
    application.processEvents()
    assert window.pages.currentWidget() is window.scan_page
    assert window.workflow_step_buttons[0].isChecked()
    window.close()


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
