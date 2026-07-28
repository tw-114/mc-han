from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication, QLabel

from mc_han.models import ExtractedText
from mc_han.qt.main_window import MainWindow
from mc_han.qt.translation_config_view_models import (
    TranslationProvider,
    TranslationSessionConfig,
)
from mc_han.qt.view_models import WorkflowStage
from mc_han.scanner import ScanRecords
from mc_han.services.scan_service import classify_scan_records
from mc_han.usage.models import TokenUsage, TranslationUsageSummary
from mc_han.workflow.models import (
    CAPABILITY_ORDER,
    ChineseResourceStatus,
    ContentCapability,
    ExistingChineseResources,
    InspectionValidity,
    LoaderInfo,
    ModpackInspection,
)
from mc_han.workflow.scan_models import ScanSelectionState
from mc_han.workflow.trial_models import (
    TrialSampleResult,
    TrialSampleStatus,
    TrialTranslationResult,
)


@pytest.fixture(scope="module")
def application():
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


def _process_until(application: QApplication, predicate, timeout: float = 3) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        if predicate():
            return
        QThread.msleep(5)
    raise AssertionError("Qt condition was not reached before timeout")


def _records() -> list[ExtractedText]:
    return [
        ExtractedText(
            id=f"sample-{index}",
            source_type="jar_lang",
            container="mods/demo.jar",
            file_path=f"assets/demo/{index}.json",
            key_path=f"key.{index}",
            original=f"Original {index}",
        )
        for index in range(8)
    ]


def _samples() -> tuple[TrialSampleResult, ...]:
    return tuple(
        TrialSampleResult(
            text_id=record.id,
            original=record.original,
            translation="",
            category_id=classify_scan_records(
                ScanRecords(
                    [record],
                    inventory={
                        "jar_safety_diagnostics": [],
                        "resourcepack_lang_zh_cn_files_found": 0,
                    },
                ),
                scan_duration=0,
            ).categories[0].category_id,
            category_title="模组语言文件",
            source=record.file_path,
        )
        for record in _records()
    )


def _window(tmp_path: Path, *, trial_service) -> MainWindow:
    samples = _samples()
    window = MainWindow(
        trial_prepare_service=lambda _path, _selection: samples,
        trial_service=trial_service,
    )
    window.current_inspection = ModpackInspection(
        input_directory=tmp_path,
        validity=InspectionValidity.VALID,
        display_name="Trial Pack",
        minecraft_version="1.20.1",
        loader=LoaderInfo("NeoForge", "47.1.0"),
        mod_count=1,
        capabilities=tuple(
            ContentCapability(key, key, detected=False)
            for key in CAPABILITY_ORDER
        ),
        existing_chinese=ExistingChineseResources(
            ChineseResourceStatus.NONE
        ),
        messages=(),
        evidence=(),
        inspection_duration=0.1,
    )
    scan_result = classify_scan_records(
        ScanRecords(
            _records(),
            inventory={
                "jar_safety_diagnostics": [],
                "resourcepack_lang_zh_cn_files_found": 0,
            },
        ),
        scan_duration=0.1,
    )
    window.scan_selection = ScanSelectionState.from_result(
        scan_result
    ).select_all()
    window.translation_session_config = TranslationSessionConfig(
        provider=TranslationProvider.DEEPSEEK,
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
        api_key="sk-fake-test",
    )
    window.translation_config_draft = window.translation_session_config
    window.show_translation_config()
    return window


def _close_window(application: QApplication, window: MainWindow) -> None:
    _process_until(
        application,
        lambda: window._thread_pool.activeThreadCount() == 0,
    )
    window.close()
    application.processEvents()


def _success_result(
    samples: tuple[TrialSampleResult, ...],
    task_id: str,
) -> TrialTranslationResult:
    return TrialTranslationResult(
        samples=tuple(
            TrialSampleResult(
                text_id=item.text_id,
                original=item.original,
                translation=f"译文 {item.text_id}",
                category_id=item.category_id,
                category_title=item.category_title,
                source=item.source,
                status=TrialSampleStatus.SUCCESS,
            )
            for item in samples
        ),
        usage=TranslationUsageSummary(
            api_attempts=1,
            successful_attempts=1,
            translated_items=len(samples),
            input_tokens=20,
            output_tokens=10,
            total_tokens=30,
            reported_cost_total=None,
            reported_cost_complete=False,
            missing_reported_cost_count=1,
        ),
        elapsed_seconds=0.2,
        task_id=task_id,
    )


def test_provider_is_not_called_until_user_clicks_start(
    application: QApplication,
    tmp_path: Path,
):
    calls = []

    def fake_trial(_path, _config, samples, **kwargs):
        calls.append(kwargs.get("target_ids"))
        return _success_result(samples, kwargs["task_id"])

    window = _window(tmp_path, trial_service=fake_trial)
    window.save_translation_config_and_continue()
    application.processEvents()

    assert calls == []
    assert window.stage is WorkflowStage.TRIAL_TRANSLATION
    assert window.trial_translation_page.start_button.isEnabled()
    labels = {
        label.text()
        for label in window.trial_translation_page.findChildren(QLabel)
    }
    assert any("产生少量 API 费用" in text for text in labels)

    window.trial_translation_page.start_button.click()
    _process_until(application, lambda: window.trial_result is not None)
    assert calls == [None]
    assert window.trial_translation_page.continue_button.isEnabled()
    _close_window(application, window)


def test_retry_from_qt_passes_only_failed_ids(
    application: QApplication,
    tmp_path: Path,
):
    calls: list[frozenset[str] | None] = []

    def fake_trial(_path, _config, samples, **kwargs):
        target_ids = kwargs.get("target_ids")
        calls.append(target_ids)
        if target_ids is None:
            failed = samples[2].text_id
            result_samples = tuple(
                TrialSampleResult(
                    text_id=item.text_id,
                    original=item.original,
                    translation=(
                        "" if item.text_id == failed else f"译文 {item.text_id}"
                    ),
                    category_id=item.category_id,
                    category_title=item.category_title,
                    source=item.source,
                    status=(
                        TrialSampleStatus.FAILED
                        if item.text_id == failed
                        else TrialSampleStatus.SUCCESS
                    ),
                )
                for item in samples
            )
            return TrialTranslationResult(
                samples=result_samples,
                usage=TranslationUsageSummary(),
                elapsed_seconds=0.1,
                task_id=kwargs["task_id"],
            )
        return _success_result(samples, kwargs["task_id"])

    window = _window(tmp_path, trial_service=fake_trial)
    window.save_translation_config_and_continue()
    window.start_trial_translation()
    _process_until(
        application,
        lambda: window.trial_result is not None
        and window.trial_result.failed_count == 1,
    )
    failed_ids = window.trial_result.failed_ids

    window.retry_failed_trial_samples()
    _process_until(
        application,
        lambda: len(calls) == 2 and not window._trial_running,
    )

    assert calls == [None, failed_ids]
    assert window.trial_result.failed_count == 0
    window.confirm_trial_translation()
    assert window.stage is WorkflowStage.FULL_TRANSLATION
    _close_window(application, window)


def test_trial_provider_runs_off_main_thread_and_keeps_ui_responsive(
    application: QApplication,
    tmp_path: Path,
):
    started = threading.Event()
    release = threading.Event()
    worker_flags: list[bool] = []

    def slow_trial(_path, _config, samples, **kwargs):
        worker_flags.append(
            QThread.currentThread() is application.thread()
        )
        started.set()
        release.wait(2)
        return _success_result(samples, kwargs["task_id"])

    window = _window(tmp_path, trial_service=slow_trial)
    window.show()
    window.save_translation_config_and_continue()
    window.start_trial_translation()
    _process_until(application, started.is_set)

    assert worker_flags == [False]
    assert not window.trial_translation_page.back_button.isEnabled()
    assert not window.trial_translation_page.start_button.isEnabled()
    assert window.isVisible()

    release.set()
    _process_until(application, lambda: window.trial_result is not None)
    assert window.trial_translation_page.back_button.isEnabled()
    _close_window(application, window)
