from __future__ import annotations

from mc_han.models import ExtractedText
from mc_han.usage.estimate import estimate_selected_work
from mc_han.workflow.scan_models import (
    ScanCategoryId,
    ScanCategorySummary,
    ScanClassificationResult,
    ScanSelectionState,
)


def test_estimate_selected_work_is_explicitly_character_based():
    records = [
        record("one", "12345", "jar_lang"),
        record("two", "1234567", "jar_patchouli"),
        record("three", "123", "jar_lang", translation="已有"),
    ]
    selection = selection_for(records).set_selected(
        ScanCategoryId.PATCHOULI,
        False,
    )

    estimate = estimate_selected_work(
        records,
        selection,
        cached_record_ids={"one"},
    )

    assert estimate.selected_record_count == 2
    assert estimate.untranslated_record_count == 0
    assert estimate.cached_record_count == 1
    assert estimate.skipped_record_count == 0
    assert estimate.estimated_input_characters == 0
    assert estimate.estimated_output_characters == 0
    assert estimate.to_dict()["unit"] == "characters"


def test_estimate_uses_scan_selection_mapping_without_reclassifying():
    records = [
        record("known", "abcd", "jar_lang"),
        record("unknown", "abcdef", "future_supported_type"),
    ]
    selection = selection_for(records)

    estimate = estimate_selected_work(records, selection)

    assert estimate.selected_record_count == 1
    assert estimate.untranslated_record_count == 1
    assert estimate.estimated_input_characters == 4


def test_estimate_excludes_structured_and_legacy_skipped_records():
    records = [
        record("normal", "12345", "jar_lang"),
        record(
            "structured",
            "1234567",
            "jar_lang",
            skip_status="user_skipped",
        ),
        record(
            "legacy",
            "123",
            "jar_lang",
            note="skip",
        ),
    ]
    selection = selection_for(records)

    estimate = estimate_selected_work(records, selection)

    assert estimate.selected_record_count == 3
    assert estimate.skipped_record_count == 2
    assert estimate.untranslated_record_count == 1
    assert estimate.estimated_input_characters == 5


def selection_for(records: list[ExtractedText]) -> ScanSelectionState:
    language_records = [
        record for record in records if record.source_type == "jar_lang"
    ]
    patchouli_records = [
        record for record in records if record.source_type == "jar_patchouli"
    ]
    other_records = [
        record
        for record in records
        if record.source_type not in {"jar_lang", "jar_patchouli"}
    ]
    categories = []
    for category_id, matching, selected in (
        (ScanCategoryId.MOD_LANGUAGE, language_records, True),
        (ScanCategoryId.PATCHOULI, patchouli_records, True),
        (ScanCategoryId.OTHER_SUPPORTED, other_records, False),
    ):
        if not matching:
            continue
        categories.append(
            ScanCategorySummary(
                category_id=category_id,
                title=category_id.value,
                description="test",
                translatable=True,
                default_selected=selected,
                record_count=len(matching),
                file_count=len(matching),
                source_count=1,
                selected=selected,
                disabled_reason="",
                source_types=tuple(
                    sorted({record.source_type for record in matching})
                ),
                sources=("mods/demo.jar",),
            )
        )
    result = ScanClassificationResult(
        categories=tuple(categories),
        diagnostics=(),
        total_translatable_records=len(records),
        total_file_count=len(records),
        total_source_count=1,
        scan_duration=0,
        output_csv=".mc-han/extracted_texts.csv",
        report_path=".mc-han/logs/scan_report.txt",
    )
    return ScanSelectionState.from_result(result)


def record(
    identifier: str,
    original: str,
    source_type: str,
    *,
    translation: str = "",
    note: str = "",
    skip_status: str = "",
) -> ExtractedText:
    return ExtractedText(
        id=identifier,
        source_type=source_type,
        container="mods/demo.jar",
        file_path=f"assets/demo/{identifier}.json",
        key_path=identifier,
        original=original,
        translation=translation,
        note=note,
        skip_status=skip_status,
    )
