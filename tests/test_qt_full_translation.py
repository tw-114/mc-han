from __future__ import annotations

import os
import threading
import time
from decimal import Decimal
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication, QPushButton

from mc_han.core.project import project_paths
from mc_han.csv_store import read_extracted_csv, write_extracted_csv
from mc_han.models import ExtractedText
from mc_han.qt.main_window import MainWindow
from mc_han.qt.translation_config_view_models import (
    TranslationProvider,
    TranslationSessionConfig,
)
from mc_han.qt.view_models import WorkflowStage
from mc_han.scanner import ScanRecords
from mc_han.services.scan_service import classify_scan_records
from mc_han.translator.base import TranslationSegment
from mc_han.translator.usage import (
    ProviderAttemptError,
    ProviderAttemptResult,
    UsageNormalizationResult,
)
from mc_han.usage.models import (
    TokenUsage,
    TranslationUsageSummary,
    UsageOutcome,
)
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
    ScanCategoryId,
    ScanSelectionState,
)
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


class FakeFullProvider:
    is_network_provider = True
    provider_name = "deepseek"
    model = "deepseek-chat"
    endpoint_type = "chat_completions"
    thinking_mode = ""

    def __init__(
        self,
        *,
        failed_ids: set[str] | None = None,
        started: threading.Event | None = None,
        release: threading.Event | None = None,
    ) -> None:
        self.failed_ids = failed_ids or set()
        self.started = started
        self.release = release
        self.calls: list[tuple[str, ...]] = []
        self.called_on_main_thread: list[bool] = []
        self.application_thread = QApplication.instance().thread()

    def translate_batch_with_usage(
        self,
        segments: list[TranslationSegment],
    ) -> ProviderAttemptResult:
        ids = tuple(segment.id for segment in segments)
        self.calls.append(ids)
        self.called_on_main_thread.append(
            QThread.currentThread() is self.application_thread
        )
        if self.started is not None:
            self.started.set()
        if self.release is not None:
            self.release.wait(2)
        if any(text_id in self.failed_ids for text_id in ids):
            raise ProviderAttemptError(
                outcome=UsageOutcome.CANCELLED,
                stable_error_code="fake_failed",
                retryable=False,
                usage=_usage(),
            )
        return ProviderAttemptResult(
            translations=tuple(
                f"译文 {segment.id}" for segment in segments
            ),
            usage=_usage(),
        )


def _usage() -> UsageNormalizationResult:
    return UsageNormalizationResult(
        tokens=TokenUsage(
            input_tokens=20,
            output_tokens=10,
            total_tokens=30,
        ),
        provider_reported_cost=Decimal("0.001"),
        currency="USD",
    )


def _record(
    text_id: str,
    *,
    source_type: str = "jar_lang",
    translation: str = "",
) -> ExtractedText:
    return ExtractedText(
        id=text_id,
        source_type=source_type,
        container="mods/demo.jar",
        file_path=f"assets/demo/{text_id}.json",
        key_path=f"demo.{text_id}",
        original=f"Original {text_id}",
        translation=translation,
    )


def _inspection(path: Path) -> ModpackInspection:
    return ModpackInspection(
        input_directory=path,
        validity=InspectionValidity.VALID,
        display_name="Full Translation Pack",
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


def _selection(records: list[ExtractedText]) -> ScanSelectionState:
    result = classify_scan_records(
        ScanRecords(
            records,
            inventory={
                "jar_safety_diagnostics": [],
                "resourcepack_lang_zh_cn_files_found": 0,
            },
        ),
        scan_duration=0.1,
    )
    return ScanSelectionState.from_result(result).select_all()


def _trial_result(record: ExtractedText) -> TrialTranslationResult:
    return TrialTranslationResult(
        samples=(
            TrialSampleResult(
                text_id=record.id,
                original=record.original,
                translation=record.translation,
                category_id=ScanCategoryId.MOD_LANGUAGE,
                category_title="模组语言文件",
                source=record.file_path,
                status=TrialSampleStatus.SUCCESS,
            ),
        ),
        usage=TranslationUsageSummary(),
        elapsed_seconds=0.1,
        task_id="trial-test",
    )


def _window(
    tmp_path: Path,
    records: list[ExtractedText],
    provider: FakeFullProvider,
) -> MainWindow:
    paths = project_paths(tmp_path)
    write_extracted_csv(records, paths.extracted_csv)
    window = MainWindow(
        full_translator_factory=lambda _config: provider,
    )
    window.current_inspection = _inspection(tmp_path)
    window.scan_selection = _selection(records)
    window.current_scan_result = window.scan_selection.result
    window.translation_session_config = TranslationSessionConfig(
        provider=TranslationProvider.DEEPSEEK,
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
        api_key="sk-fake-test",
        concurrency=1,
        batch_size=1,
    )
    window.translation_config_draft = window.translation_session_config
    translated = next(record for record in records if record.translation)
    window.trial_result = _trial_result(translated)
    window.trial_samples = window.trial_result.samples
    window.stage = WorkflowStage.TRIAL_TRANSLATION
    window.pages.setCurrentWidget(window.trial_translation_page)
    return window


def _process_until(
    application: QApplication,
    predicate,
    *,
    timeout: float = 5,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        if predicate():
            return
        QThread.msleep(5)
    raise AssertionError("Qt condition was not reached before timeout")


def _close_window(
    application: QApplication,
    window: MainWindow,
) -> None:
    _process_until(
        application,
        lambda: window._thread_pool.activeThreadCount() == 0,
    )
    window.close()
    application.processEvents()


def test_full_translation_uses_existing_engine_after_user_click(
    application: QApplication,
    tmp_path: Path,
):
    started = threading.Event()
    release = threading.Event()
    provider = FakeFullProvider(started=started, release=release)
    records = [
        _record("trial", translation="试译结果"),
        _record("pending", source_type="jar_patchouli"),
    ]
    window = _window(tmp_path, records, provider)
    window.show()

    window.confirm_trial_translation()
    application.processEvents()

    assert window.stage is WorkflowStage.FULL_TRANSLATION
    assert provider.calls == []
    assert window.full_translation_page.start_button.isEnabled()

    window.start_full_translation()
    _process_until(application, started.is_set)

    assert provider.calls == [("pending",)]
    assert provider.called_on_main_thread == [False]
    assert window._full_running
    _process_until(
        application,
        lambda: (
            window.full_translation_page.summary_values[
                "category"
            ].text()
            == "Patchouli 手册"
        ),
    )
    assert window.full_translation_page.summary_values[
        "category"
    ].text() == "Patchouli 手册"

    release.set()
    _process_until(
        application,
        lambda: (
            window.stage
            is WorkflowStage.TRANSLATION_REVIEW
        ),
    )

    saved = {
        record.id: record
        for record in read_extracted_csv(
            project_paths(tmp_path).extracted_csv
        )
    }
    assert saved["trial"].translation == "试译结果"
    assert saved["pending"].translation == "译文 pending"
    window.show_full_translation_result()
    assert (
        window.full_translation_page.summary_values["completed"].text()
        == "2 / 2"
    )
    assert (
        window.full_translation_page.summary_values["tokens"].text()
        == "30"
    )
    assert "0.001000 USD" in (
        window.full_translation_page.summary_values["cost"].text()
    )
    _close_window(application, window)


def test_retry_only_sends_failed_records_to_existing_engine(
    application: QApplication,
    tmp_path: Path,
):
    provider = FakeFullProvider(failed_ids={"failed"})
    records = [
        _record("trial", translation="试译结果"),
        _record("success"),
        _record("failed", source_type="jar_patchouli"),
    ]
    window = _window(tmp_path, records, provider)
    window.confirm_trial_translation()
    window.start_full_translation()
    _process_until(
        application,
        lambda: window._full_result is not None
        and not window._full_running,
    )

    assert window.stage is WorkflowStage.FULL_TRANSLATION
    assert window._full_result.failed_ids == frozenset({"failed"})
    assert window.full_translation_page.retry_button.isEnabled()
    calls_before_retry = tuple(provider.calls)

    provider.failed_ids.clear()
    window.retry_failed_full_translation()
    _process_until(
        application,
        lambda: (
            window.stage
            is WorkflowStage.TRANSLATION_REVIEW
        ),
    )

    assert calls_before_retry == (("success",), ("failed",))
    assert provider.calls[-1] == ("failed",)
    saved = {
        record.id: record
        for record in read_extracted_csv(
            project_paths(tmp_path).extracted_csv
        )
    }
    assert saved["failed"].translation == "译文 failed"
    assert saved["failed"].note == ""
    _close_window(application, window)


def test_completed_trial_selection_skips_full_provider(
    application: QApplication,
    tmp_path: Path,
):
    provider = FakeFullProvider()
    records = [_record("trial", translation="试译结果")]
    window = _window(tmp_path, records, provider)

    window.confirm_trial_translation()
    application.processEvents()

    assert provider.calls == []
    assert (
        window.stage
        is WorkflowStage.TRANSLATION_REVIEW
    )
    _close_window(application, window)


def test_settings_preserves_running_translation_page_and_single_task(
    application: QApplication,
    tmp_path: Path,
):
    started = threading.Event()
    release = threading.Event()
    provider = FakeFullProvider(started=started, release=release)
    records = [
        _record("trial", translation="试译结果"),
        _record("pending"),
    ]
    window = _window(tmp_path, records, provider)
    window.confirm_trial_translation()
    window.start_full_translation()
    _process_until(application, started.is_set)

    window.settings_button.click()
    application.processEvents()

    assert window.pages.currentWidget() is window.settings_page
    assert window.stage is WorkflowStage.FULL_TRANSLATION
    assert window.session.active_task is not None
    assert window.session.active_task.label == "完整翻译"
    assert window.activity_label.text() == "活动任务：完整翻译"
    assert all(
        button.toolTip()
        for button in window.findChildren(QPushButton)
        if not button.isEnabled()
    )

    window.start_full_translation()
    application.processEvents()
    assert provider.calls == [("pending",)]
    assert window._notice_boxes

    window.settings_page.back_button.click()
    application.processEvents()
    assert window.pages.currentWidget() is window.full_translation_page
    assert window.stage is WorkflowStage.FULL_TRANSLATION

    release.set()
    _process_until(
        application,
        lambda: window.stage is WorkflowStage.TRANSLATION_REVIEW,
    )
    assert window.session.active_task is None
    _close_window(application, window)


def test_full_translation_failure_releases_task_and_restores_start(
    application: QApplication,
    tmp_path: Path,
):
    provider = FakeFullProvider()
    records = [
        _record("trial", translation="试译结果"),
        _record("pending"),
    ]
    window = _window(tmp_path, records, provider)

    def fail_factory(_config):
        raise RuntimeError("simulated translator creation failure")

    window._full_translator_factory = fail_factory
    window.confirm_trial_translation()
    window.start_full_translation()
    _process_until(
        application,
        lambda: not window._full_running,
    )
    application.processEvents()

    assert provider.calls == []
    assert window.session.active_task is None
    assert window.full_translation_page.start_button.isEnabled()
    assert window.activity_label.text() == "当前无活动任务"
    _close_window(application, window)
