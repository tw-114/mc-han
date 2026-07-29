from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from mc_han import csv_store
from mc_han.core.project import project_paths
from mc_han.csv_store import read_extracted_csv, write_extracted_csv
from mc_han.models import ExtractedText
from mc_han.qt.main_window import MainWindow
from mc_han.qt.translation_review_view_models import ReviewFilterId
from mc_han.qt.view_models import WorkflowStage
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


def _record(
    text_id: str,
    *,
    translation: str = "",
    note: str = "",
    review_status: str = "",
    skip_status: str = "",
    source_type: str = "jar_lang",
) -> ExtractedText:
    return ExtractedText(
        id=text_id,
        source_type=source_type,
        container=f"mods/{text_id}.jar",
        file_path=f"assets/{text_id}/lang/en_us.json",
        key_path=f"{text_id}.text",
        original=f"Original {text_id} %s",
        translation=translation,
        note=note,
        review_status=review_status,
        skip_status=skip_status,
    )


def _inspection(path: Path) -> ModpackInspection:
    return ModpackInspection(
        input_directory=path,
        validity=InspectionValidity.VALID,
        display_name="Review Pack",
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


def _window(
    tmp_path: Path,
    records: list[ExtractedText],
) -> MainWindow:
    write_extracted_csv(
        records,
        project_paths(tmp_path).extracted_csv,
    )
    window = MainWindow()
    window.current_inspection = _inspection(tmp_path)
    window.show_translation_review()
    return window


def _select(window: MainWindow, text_id: str) -> None:
    for index, row in enumerate(
        window.translation_review_page.table_model.rows
    ):
        if row.text_id == text_id:
            window.translation_review_page.table.selectRow(index)
            QApplication.processEvents()
            return
    raise AssertionError(f"record not visible: {text_id}")


def test_qt_review_edits_filters_statuses_and_opens_build_page(
    application: QApplication,
    tmp_path: Path,
):
    records = [
        _record("edit", translation="旧译文 %s"),
        _record("failed", note="failed: fake"),
        _record("reviewed", translation="审核 %s", review_status="approved"),
        _record("skipped", translation="跳过 %s", skip_status="user_skipped"),
        _record("book", translation="手册 %s", source_type="jar_patchouli"),
    ]
    window = _window(tmp_path, records)
    page = window.translation_review_page

    assert window.stage is WorkflowStage.TRANSLATION_REVIEW
    assert page.filter_combo.currentData() == ReviewFilterId.ISSUES.value
    assert [row.text_id for row in page.table_model.rows] == ["failed"]
    page.filter_combo.setCurrentIndex(
        page.filter_combo.findData(ReviewFilterId.ALL.value)
    )
    assert page.table_model.rowCount() == 5
    page.search_input.setText("Patchouli")
    application.processEvents()
    assert [row.text_id for row in page.table_model.rows] == ["book"]
    page.search_input.clear()
    page.filter_combo.setCurrentIndex(
        page.filter_combo.findData(ReviewFilterId.FAILED.value)
    )
    application.processEvents()
    assert [row.text_id for row in page.table_model.rows] == ["failed"]
    page.filter_combo.setCurrentIndex(
        page.filter_combo.findData(ReviewFilterId.ALL.value)
    )

    _select(window, "edit")
    page.translation_editor.setPlainText("人工修改 %s")
    page.save_button.click()
    application.processEvents()
    saved = {
        record.id: record
        for record in read_extracted_csv(
            project_paths(tmp_path).extracted_csv
        )
    }
    assert saved["edit"].translation == "人工修改 %s"
    assert saved["failed"].note == "failed: fake"

    page.approve_button.click()
    application.processEvents()
    saved = {
        record.id: record
        for record in read_extracted_csv(
            project_paths(tmp_path).extracted_csv
        )
    }
    assert saved["edit"].review_status == "approved"

    _select(window, "book")
    page.needs_retranslate_button.click()
    application.processEvents()
    saved = {
        record.id: record
        for record in read_extracted_csv(
            project_paths(tmp_path).extracted_csv
        )
    }
    assert saved["book"].translation == ""
    assert saved["book"].review_status == "needs_retranslate"

    _select(window, "failed")
    page.skip_button.click()
    application.processEvents()
    saved = {
        record.id: record
        for record in read_extracted_csv(
            project_paths(tmp_path).extracted_csv
        )
    }
    assert saved["failed"].skip_status == "user_skipped"

    page.retranslate_button.click()
    assert "没有调用 API" in page.feedback_label.text()
    page.continue_button.click()
    assert window.stage is WorkflowStage.BUILD_INSTALL
    assert window.pages.currentWidget() is window.build_install_page
    window.build_install_page.back_button.click()
    assert window.stage is WorkflowStage.TRANSLATION_REVIEW
    window.close()
    application.processEvents()


def test_qt_review_save_failure_keeps_editor_and_old_csv(
    application: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    window = _window(
        tmp_path,
        [_record("edit", translation="原有译文 %s")],
    )
    page = window.translation_review_page
    page.filter_combo.setCurrentIndex(
        page.filter_combo.findData(ReviewFilterId.ALL.value)
    )
    _select(window, "edit")
    csv_path = project_paths(tmp_path).extracted_csv
    before = csv_path.read_bytes()

    def fail_replace(_source, _target):
        raise PermissionError("simulated")

    monkeypatch.setattr(csv_store.os, "replace", fail_replace)
    page.translation_editor.setPlainText("无法保存 %s")
    page.save_button.click()
    application.processEvents()

    assert csv_path.read_bytes() == before
    assert page.translation_editor.toPlainText() == "无法保存 %s"
    assert "原有译文清单已经保留" in page.feedback_label.text()
    assert not list(csv_path.parent.glob(".mc-han-csv-*.tmp"))
    window.close()
    application.processEvents()
