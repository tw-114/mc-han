from __future__ import annotations

import inspect
import os
import threading
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QThread, QTimer
from PySide6.QtWidgets import QApplication, QLabel, QScrollArea

from mc_han.models import ExtractedText
from mc_han.qt.main_window import MainWindow
from mc_han.qt.pages.scan_page import ScanPage
from mc_han.qt.task_runner import ScanTask
from mc_han.qt.view_models import InspectionPageViewModel, WorkflowStage
from mc_han.scanner import ScanRecords
from mc_han.services.scan_service import ScanServiceError, classify_scan_records
from mc_han.workflow.models import (
    CAPABILITY_ORDER,
    ChineseResourceStatus,
    ContentCapability,
    ExistingChineseResources,
    InspectionValidity,
    LoaderInfo,
    ModpackInspection,
)
from mc_han.workflow.scan_models import (
    ScanClassificationResult,
    ScanProgressEvent,
    ScanProgressStage,
)


@pytest.fixture(scope="module")
def application():
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


def process_until(application: QApplication, predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        if predicate():
            return
        QThread.msleep(5)
    raise AssertionError("Qt condition was not reached before timeout")


def make_inspection(
    path: Path,
    validity: InspectionValidity = InspectionValidity.VALID,
) -> ModpackInspection:
    return ModpackInspection(
        input_directory=path,
        validity=validity,
        display_name="Scan Test Pack",
        minecraft_version="1.20.1",
        loader=LoaderInfo("NeoForge", "47.1.0"),
        mod_count=2,
        capabilities=tuple(
            ContentCapability(key, key, detected=False)
            for key in CAPABILITY_ORDER
        ),
        existing_chinese=ExistingChineseResources(ChineseResourceStatus.NONE),
        messages=(),
        evidence=(),
        inspection_duration=0.1,
    )


def make_scan_result() -> ScanClassificationResult:
    records = ScanRecords(
        [
            ExtractedText(
                id="lang",
                source_type="jar_lang",
                container="mods/demo.jar",
                file_path="assets/demo/lang/en_us.json",
                key_path="demo.text",
                original="Demo text",
            ),
            ExtractedText(
                id="book",
                source_type="jar_patchouli",
                container="mods/book.jar",
                file_path="assets/book/patchouli_books/demo/en_us/entry.json",
                key_path="text",
                original="Book text",
            ),
        ],
        inventory={
            "jar_safety_diagnostics": [],
            "resourcepack_lang_zh_cn_files_found": 0,
        },
    )
    return classify_scan_records(records, scan_duration=0.25)


def attach_inspection(window: MainWindow, inspection: ModpackInspection) -> None:
    window.current_inspection = inspection
    window.inspection_page.show_result(
        InspectionPageViewModel.from_inspection(inspection)
    )
    window.pages.setCurrentWidget(window.inspection_page)


@pytest.mark.parametrize(
    "validity",
    (InspectionValidity.VALID, InspectionValidity.PROBABLE),
)
def test_valid_and_probable_can_start_background_scan(
    application: QApplication,
    tmp_path: Path,
    validity: InspectionValidity,
):
    calls = []

    def fake_scan(path: Path, *, progress, existing_chinese):
        calls.append(path)
        progress(
            ScanProgressEvent(
                ScanProgressStage.SCANNING,
                "正在扫描",
                discovered_records=1,
            )
        )
        return make_scan_result()

    window = MainWindow(scan_service=fake_scan)
    attach_inspection(window, make_inspection(tmp_path, validity))
    window.start_scan()

    process_until(application, lambda: window.stage is WorkflowStage.SCAN_RESULT)
    assert calls == [tmp_path]
    assert window.pages.currentWidget() is window.scan_page
    assert window.scan_page.continue_button.isEnabled()

    window.close()
    application.processEvents()


def test_invalid_inspection_cannot_start_scan(
    application: QApplication,
    tmp_path: Path,
):
    calls = []

    def fake_scan(*args, **kwargs):
        calls.append(args)
        return make_scan_result()

    window = MainWindow(scan_service=fake_scan)
    attach_inspection(
        window,
        make_inspection(tmp_path, InspectionValidity.INVALID),
    )
    window.start_scan()
    application.processEvents()

    assert calls == []
    assert not window._scan_running
    window.close()


def test_scan_starts_once_and_buttons_are_disabled_while_running(
    application: QApplication,
    tmp_path: Path,
):
    release = threading.Event()
    calls = []

    def slow_scan(path: Path, *, progress, existing_chinese):
        calls.append(path)
        progress(
            ScanProgressEvent(
                ScanProgressStage.SCANNING,
                "正在扫描正文",
            )
        )
        release.wait(2)
        return make_scan_result()

    window = MainWindow(scan_service=slow_scan)
    attach_inspection(window, make_inspection(tmp_path))
    window.start_scan()
    window.start_scan()
    process_until(application, lambda: window.scan_page.loading_stage.text() == "正在扫描正文")

    assert calls == [tmp_path]
    assert not window.scan_page.back_button.isEnabled()
    assert not window.scan_page.rescan_button.isEnabled()
    assert not window.scan_page.continue_button.isEnabled()

    release.set()
    process_until(application, lambda: window.stage is WorkflowStage.SCAN_RESULT)
    window.close()


def test_category_selection_updates_total_and_opens_translation_config(
    application: QApplication,
    tmp_path: Path,
):
    window = MainWindow(scan_service=lambda *args, **kwargs: make_scan_result())
    attach_inspection(window, make_inspection(tmp_path))
    window.start_scan()
    process_until(application, lambda: window.stage is WorkflowStage.SCAN_RESULT)

    assert window.scan_page.selection_summary.text() == (
        "已选择 2 条，共 2 条可翻译内容"
    )
    window.clear_scan_categories()
    application.processEvents()
    assert window.scan_page.selection_summary.text() == (
        "已选择 0 条，共 2 条可翻译内容"
    )
    assert not window.scan_page.continue_button.isEnabled()

    window.restore_scan_category_defaults()
    window.show_translation_config()
    assert window.stage is WorkflowStage.TRANSLATION_CONFIG
    labels = {
        label.text()
        for label in window.translation_config_page.findChildren(QLabel)
    }
    assert "翻译服务配置" in labels
    assert "已选择 2 条内容，共 2 个分类" in labels

    window.close()


def test_scan_failure_can_retry(
    application: QApplication,
    tmp_path: Path,
):
    calls = {"count": 0}

    def flaky_scan(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise ScanServiceError(
                code="scan_failed",
                message="扫描未能完成，请检查目录后重试。",
                detail="PermissionError",
                retryable=True,
                partial_saved=False,
            )
        return make_scan_result()

    window = MainWindow(scan_service=flaky_scan)
    attach_inspection(window, make_inspection(tmp_path))
    window.start_scan()
    process_until(application, lambda: not window._scan_running)

    assert not window.scan_page.failure_card.isHidden()
    assert window.scan_page.rescan_button.isEnabled()
    assert "Traceback" not in window.scan_page.failure_message.text()

    window.start_scan()
    process_until(application, lambda: window.stage is WorkflowStage.SCAN_RESULT)
    assert calls["count"] == 2
    window.close()


def test_scan_page_is_scrollable_at_small_size(
    application: QApplication,
    tmp_path: Path,
):
    window = MainWindow(scan_service=lambda *args, **kwargs: make_scan_result())
    attach_inspection(window, make_inspection(tmp_path))
    window.start_scan()
    process_until(application, lambda: window.stage is WorkflowStage.SCAN_RESULT)
    window.resize(920, 620)
    window.show()
    application.processEvents()

    assert isinstance(window.scan_page, QScrollArea)
    assert window.scan_page.verticalScrollBar().maximum() > 0
    window.close()


def test_scan_worker_source_has_no_widget_access():
    source = inspect.getsource(ScanTask)

    assert "QWidget" not in source
    assert "MainWindow" not in source


def test_close_during_scan_returns_immediately_and_closes_when_worker_finishes(
    application: QApplication,
    tmp_path: Path,
):
    started = threading.Event()
    release = threading.Event()
    heartbeat = threading.Event()
    calls = []

    def slow_scan(*args, **kwargs):
        calls.append(args)
        started.set()
        release.wait(2)
        return make_scan_result()

    window = MainWindow(scan_service=slow_scan)
    attach_inspection(window, make_inspection(tmp_path))
    window.show()
    window.start_scan()
    process_until(application, started.is_set)

    before = time.monotonic()
    window.close()
    elapsed = time.monotonic() - before
    QTimer.singleShot(0, heartbeat.set)
    application.processEvents()

    assert elapsed < 0.1
    assert window.isVisible()
    assert window._close_when_idle
    assert heartbeat.is_set()
    assert window.footer_status.text() == "正在安全结束当前扫描，完成后将自动关闭"

    window.start_scan()
    window.start_inspection(tmp_path / "other")
    assert len(calls) == 1

    release.set()
    process_until(
        application,
        lambda: not window.isVisible()
        and window._thread_pool.activeThreadCount() == 0,
    )
    assert not window._close_when_idle


def test_close_pending_finishes_after_scan_failure(
    application: QApplication,
    tmp_path: Path,
):
    started = threading.Event()
    release = threading.Event()

    def failing_scan(*args, **kwargs):
        started.set()
        release.wait(2)
        raise ScanServiceError(
            code="scan_failed",
            message="扫描失败。",
            detail="OSError",
        )

    window = MainWindow(scan_service=failing_scan)
    attach_inspection(window, make_inspection(tmp_path))
    window.show()
    window.start_scan()
    process_until(application, started.is_set)
    window.close()

    assert window.isVisible()
    release.set()
    process_until(
        application,
        lambda: not window.isVisible()
        and window._thread_pool.activeThreadCount() == 0,
    )


def test_close_without_active_task_is_immediate(
    application: QApplication,
):
    window = MainWindow()
    window.show()
    application.processEvents()

    window.close()
    application.processEvents()

    assert not window.isVisible()
    assert not window._close_when_idle


def test_close_event_does_not_synchronously_wait_for_thread_pool():
    source = inspect.getsource(MainWindow.closeEvent)

    assert "waitForDone" not in source
    assert "event.ignore()" in source


def test_scan_page_class_is_dedicated_scrollable_view():
    assert issubclass(ScanPage, QScrollArea)
