from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLabel, QLineEdit

from mc_han.models import ExtractedText
from mc_han.qt.main_window import MainWindow
from mc_han.qt.translation_config_view_models import TranslationProvider
from mc_han.qt.view_models import WorkflowStage
from mc_han.scanner import ScanRecords
from mc_han.services.scan_service import classify_scan_records
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


@pytest.fixture(scope="module")
def application():
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


def _inspection(path: Path) -> ModpackInspection:
    return ModpackInspection(
        input_directory=path,
        validity=InspectionValidity.VALID,
        display_name="Config Test Pack",
        minecraft_version="1.20.1",
        loader=LoaderInfo("NeoForge", "47.1.0"),
        mod_count=2,
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


def _selection() -> ScanSelectionState:
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
                file_path=(
                    "assets/book/patchouli_books/demo/en_us/entry.json"
                ),
                key_path="text",
                original="Book text",
            ),
        ],
        inventory={
            "jar_safety_diagnostics": [],
            "resourcepack_lang_zh_cn_files_found": 0,
        },
    )
    return ScanSelectionState.from_result(
        classify_scan_records(records, scan_duration=0.1)
    )


def _window_with_scan(tmp_path: Path) -> MainWindow:
    window = MainWindow()
    window.current_inspection = _inspection(tmp_path)
    window.scan_selection = _selection()
    window.current_scan_result = window.scan_selection.result
    window.show_translation_config()
    return window


def test_translation_config_page_shows_selection_and_password_control(
    application: QApplication,
    tmp_path: Path,
):
    window = _window_with_scan(tmp_path)

    assert window.stage is WorkflowStage.TRANSLATION_CONFIG
    assert window.translation_config_page.selection_summary.text() == (
        "已选择 2 条内容，共 2 个分类"
    )
    assert (
        window.translation_config_page.api_key_edit.echoMode()
        is QLineEdit.EchoMode.Password
    )
    window.translation_config_page.toggle_key_button.click()
    assert (
        window.translation_config_page.api_key_edit.echoMode()
        is QLineEdit.EchoMode.Normal
    )

    window.close()
    application.processEvents()


def test_return_to_scan_preserves_selection_and_session_draft(
    application: QApplication,
    tmp_path: Path,
):
    window = _window_with_scan(tmp_path)
    selected_ids = window.scan_selection.selected_category_ids
    window.translation_config_page.api_key_edit.setText("sk-session-only")
    window.translation_config_page.model_edit.setText("edited-model")

    window.return_to_scan_from_translation_config()

    assert window.stage is WorkflowStage.SCAN_RESULT
    assert window.scan_selection.selected_category_ids == selected_ids
    window.show_translation_config()
    assert window.translation_config_page.api_key_edit.text() == (
        "sk-session-only"
    )
    assert window.translation_config_page.model_edit.text() == "edited-model"

    window.close()
    application.processEvents()


def test_provider_change_restores_provider_defaults_but_retains_key(
    application: QApplication,
    tmp_path: Path,
):
    window = _window_with_scan(tmp_path)
    page = window.translation_config_page
    page.api_key_edit.setText("sk-session-only")

    page.provider_combo.setCurrentIndex(
        page.provider_combo.findData(TranslationProvider.OPENAI.value)
    )
    application.processEvents()

    assert page.base_url_edit.text() == "https://api.openai.com/v1"
    assert page.model_edit.text() == "gpt-4o-mini"
    assert page.api_key_edit.text() == "sk-session-only"
    window.close()


def test_validation_and_save_enter_trial_placeholder_without_writing_config(
    application: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    config_path = tmp_path / "must-not-exist.json"
    monkeypatch.setenv("MC_HAN_CONFIG", str(config_path))
    window = _window_with_scan(tmp_path)
    page = window.translation_config_page
    page.api_key_edit.setText("sk-session-only")

    window.validate_current_translation_config()
    assert "配置有效" in page.validation_label.text()
    assert window.pages.currentWidget() is page

    window.save_translation_config_and_continue()

    assert window.stage is WorkflowStage.TRIAL_TRANSLATION_PLACEHOLDER
    assert window.pages.currentWidget() is window.trial_translation_page
    labels = {
        label.text()
        for label in window.trial_translation_page.findChildren(QLabel)
    }
    assert "配置已完成，下一步将进行小批量试译。" in labels
    assert window.translation_session_config is not None
    assert window.translation_session_config.api_key == "sk-session-only"
    assert not config_path.exists()

    window.close()
    application.processEvents()


def test_invalid_configuration_stays_on_page(
    application: QApplication,
    tmp_path: Path,
):
    window = _window_with_scan(tmp_path)
    page = window.translation_config_page
    page.api_key_edit.clear()

    window.save_translation_config_and_continue()

    assert window.stage is WorkflowStage.TRANSLATION_CONFIG
    assert window.pages.currentWidget() is page
    assert "必填" in page.validation_label.text()
    assert window.translation_session_config is None
    window.close()
