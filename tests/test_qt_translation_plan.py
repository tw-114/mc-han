from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from mc_han.csv_store import write_extracted_csv
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
from mc_han.workflow.translation_plan import TranslationPlanMode


@pytest.fixture(scope="module")
def application():
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


def _window(tmp_path: Path) -> MainWindow:
    record = ExtractedText(
        id="demo",
        source_type="jar_lang",
        container="mods/demo.jar",
        file_path="assets/demo/lang/en_us.json",
        key_path="demo.text",
        original="Demo text",
    )
    state = tmp_path / ".mc-han"
    write_extracted_csv([record], state / "extracted_texts.csv")
    inspection = ModpackInspection(
        input_directory=tmp_path,
        validity=InspectionValidity.VALID,
        display_name="Plan Pack",
        minecraft_version="1.21.1",
        loader=LoaderInfo("NeoForge", "21.1"),
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
    result = classify_scan_records(
        ScanRecords([record], inventory={}),
        scan_duration=0.1,
    )
    window = MainWindow()
    window.current_inspection = inspection
    window.current_scan_result = result
    window.scan_selection = ScanSelectionState.from_result(result)
    return window


def test_plan_page_changes_modes_without_provider_call(
    application: QApplication,
    tmp_path: Path,
):
    window = _window(tmp_path)
    calls: list[str] = []
    window._full_translator_factory = lambda _config: calls.append("api")

    window.show_translation_plan()
    assert window.stage is WorkflowStage.TRANSLATION_PLAN
    assert window.pages.currentWidget() is window.translation_plan_page
    assert calls == []
    assert window.translation_plan_page.summary_values["ai"].text() == "1 条"

    window.translation_plan_page.mode_buttons[
        TranslationPlanMode.HIGH_QUALITY
    ].click()
    application.processEvents()
    assert window.selected_translation_plan is not None
    assert (
        window.selected_translation_plan.mode
        is TranslationPlanMode.HIGH_QUALITY
    )
    assert calls == []
    window.close()


def test_advanced_config_returns_to_plan_and_preserves_selection(
    application: QApplication,
    tmp_path: Path,
):
    window = _window(tmp_path)
    selected = window.scan_selection.selected_category_ids
    window.show_translation_plan()
    window.show_translation_config_from_plan()
    assert window.pages.currentWidget() is window.translation_config_page
    window.translation_config_page.api_key_edit.setText("sk-session")
    window.translation_config_page.provider_combo.setCurrentIndex(
        window.translation_config_page.provider_combo.findData(
            TranslationProvider.DEEPSEEK.value
        )
    )

    window.save_translation_config_and_continue()

    assert window.stage is WorkflowStage.TRANSLATION_PLAN
    assert window.pages.currentWidget() is window.translation_plan_page
    assert window.scan_selection.selected_category_ids == selected
    assert window.translation_session_config is not None
    assert window.translation_session_config.api_key == "sk-session"
    window.close()


def test_budget_overrun_disables_continue(
    application: QApplication,
    tmp_path: Path,
):
    window = _window(tmp_path)
    window.show_translation_plan()
    window.change_translation_budget(window.selected_translation_plan.estimated_cost_high / 2)

    assert not window.translation_plan_page.continue_button.isEnabled()
    assert "预算" in window.translation_plan_page.continue_button.toolTip()
    window.close()
