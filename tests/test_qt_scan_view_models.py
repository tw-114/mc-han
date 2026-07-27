from __future__ import annotations

from mc_han.models import ExtractedText
from mc_han.qt.scan_view_models import (
    ScanPageViewModel,
    ScanProgressViewModel,
)
from mc_han.qt.view_models import StatusTone
from mc_han.scanner import ScanRecords
from mc_han.services.scan_service import classify_scan_records
from mc_han.utils.safe_zip import ENTRY_SIZE_LIMIT, ZipDiagnostic
from mc_han.workflow.models import (
    ChineseResourceStatus,
    ExistingChineseResources,
)
from mc_han.workflow.scan_models import (
    ScanCategoryId,
    ScanProgressEvent,
    ScanProgressStage,
    ScanSelectionState,
)


def make_records(*records: ExtractedText, diagnostics=()) -> ScanRecords:
    return ScanRecords(
        list(records),
        inventory={
            "jar_safety_diagnostics": list(diagnostics),
            "resourcepack_lang_zh_cn_files_found": 0,
        },
    )


def make_record(source_type: str, key: str) -> ExtractedText:
    return ExtractedText(
        id=key,
        source_type=source_type,
        container="mods/demo.jar",
        file_path=f"assets/demo/{source_type}.json",
        key_path=key,
        original="Demo text",
    )


def test_scan_page_view_model_formats_counts_and_fixed_order():
    result = classify_scan_records(
        make_records(
            make_record("jar_patchouli", "book"),
            make_record("jar_lang", "lang"),
            make_record("lang_name", "item.demo"),
        ),
        scan_duration=1.25,
    )
    state = ScanSelectionState.from_result(result)

    view_model = ScanPageViewModel.from_selection("Demo Pack", state)

    assert view_model.project_name == "Demo Pack"
    assert view_model.total_records == "3"
    assert view_model.total_files == "3"
    assert view_model.duration == "1.25 秒"
    assert view_model.selected_summary == "已选择 2 条，共 3 条可翻译内容"
    assert tuple(item.category_id for item in view_model.categories)[:3] == (
        ScanCategoryId.MOD_LANGUAGE,
        ScanCategoryId.FTB_QUESTS,
        ScanCategoryId.PATCHOULI,
    )
    display_names = next(
        item
        for item in view_model.categories
        if item.category_id is ScanCategoryId.DISPLAY_NAMES
    )
    assert display_names.enabled
    assert not display_names.selected


def test_existing_chinese_information_uses_non_success_unknown_tone():
    result = classify_scan_records(
        make_records(),
        existing_chinese=ExistingChineseResources(
            ChineseResourceStatus.UNKNOWN,
        ),
    )
    view_model = ScanPageViewModel.from_selection(
        "Pack",
        ScanSelectionState.from_result(result),
    )
    chinese = next(
        item
        for item in view_model.information
        if item.category_id is ScanCategoryId.EXISTING_CHINESE
    )

    assert chinese.tone is StatusTone.WARNING
    assert "无法完整判断" in chinese.description


def test_scan_diagnostic_view_model_hides_absolute_location():
    result = classify_scan_records(
        make_records(
            diagnostics=(
                (
                    "C:\\Users\\Private\\danger.jar",
                    ZipDiagnostic(
                        code=ENTRY_SIZE_LIMIT,
                        entry="assets/demo/lang/en_us.json",
                        reason="private raw reason",
                    ),
                ),
            )
        )
    )
    view_model = ScanPageViewModel.from_selection(
        "Pack",
        ScanSelectionState.from_result(result),
    )

    assert view_model.diagnostics[0].location == ""
    assert "Private" not in view_model.diagnostics[0].message


def test_scan_progress_view_model_does_not_show_unsafe_source():
    view_model = ScanProgressViewModel.from_event(
        ScanProgressEvent(
            ScanProgressStage.SCANNING,
            "正在扫描",
            current_source="/home/private/mod.jar",
            discovered_records=12,
        )
    )

    assert view_model.stage_text == "正在扫描"
    assert view_model.source_text == ""
    assert view_model.discovered_text == "已发现 12 条内容"
