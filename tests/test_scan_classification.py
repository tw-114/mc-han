from __future__ import annotations

import csv
import json
from dataclasses import replace
from pathlib import Path

import pytest

from mc_han import csv_store
from mc_han.core.project import project_paths
from mc_han.csv_store import read_extracted_csv, write_extracted_csv
from mc_han.models import ExtractedText
from mc_han.scanner import ScanRecords
from mc_han.services import scan_service
from mc_han.services.scan_service import classify_scan_records, scan_and_classify
from mc_han.utils.safe_zip import (
    BAD_ZIP,
    ENTRY_SIZE_LIMIT,
    ZipDiagnostic,
)
from mc_han.workflow.models import (
    ChineseResourceStatus,
    ExistingChineseResources,
)
from mc_han.workflow.scan_models import (
    SCAN_CATEGORY_ORDER,
    SOURCE_TYPE_TO_CATEGORY,
    ScanCategoryId,
    ScanProgressStage,
    ScanSelectionState,
    category_for_record,
)


def record(
    source_type: str,
    *,
    container: str = "mods/demo.jar",
    file_path: str = "assets/demo/lang/en_us.json",
    key_path: str = "demo.text",
    original: str = "Hello",
) -> ExtractedText:
    return ExtractedText(
        id=f"{source_type}:{key_path}",
        source_type=source_type,
        container=container,
        file_path=file_path,
        key_path=key_path,
        original=original,
    )


def scan_records(
    records: list[ExtractedText],
    *,
    diagnostics: list[tuple[str, ZipDiagnostic]] | None = None,
) -> ScanRecords:
    return ScanRecords(
        records,
        inventory={
            "jar_safety_diagnostics": diagnostics or [],
            "resourcepack_lang_zh_cn_files_found": 0,
        },
    )


@pytest.mark.parametrize(
    ("source_type", "category_id"),
    tuple(SOURCE_TYPE_TO_CATEGORY.items()),
)
def test_all_known_source_types_use_product_category(
    source_type: str,
    category_id: ScanCategoryId,
):
    result = classify_scan_records(scan_records([record(source_type)]))
    summary = next(
        item for item in result.categories if item.category_id is category_id
    )

    assert summary.record_count == 1
    assert summary.source_types == (source_type,)


def test_unknown_supported_source_type_is_not_dropped():
    result = classify_scan_records(
        scan_records([record("future_supported_source")])
    )
    other = next(
        item
        for item in result.categories
        if item.category_id is ScanCategoryId.OTHER_SUPPORTED
    )

    assert result.total_translatable_records == 1
    assert other.record_count == 1
    assert other.source_types == ("future_supported_source",)


def test_record_file_and_source_counts_have_distinct_semantics():
    records = scan_records(
        [
            record("jar_lang", key_path="one"),
            record("jar_lang", key_path="two"),
            record(
                "jar_lang",
                file_path="assets/demo/lang/extra.json",
                key_path="three",
            ),
            record(
                "resourcepack_lang",
                container="modpack",
                file_path="resourcepacks/local/assets/demo/lang/en_us.json",
                key_path="four",
            ),
        ]
    )

    result = classify_scan_records(records)
    category = next(
        item
        for item in result.categories
        if item.category_id is ScanCategoryId.MOD_LANGUAGE
    )

    assert category.record_count == 4
    assert category.file_count == 3
    assert category.source_count == 2
    assert category.sources == ("modpack", "mods/demo.jar")
    assert result.total_file_count == 3
    assert result.total_source_count == 2


def test_category_order_and_default_selection_are_stable():
    result = classify_scan_records(
        scan_records(
            [
                record("jar_patchouli"),
                record("jar_lang", key_path="language"),
                record("lang_name", key_path="item.demo"),
                record("future_supported_source", key_path="future"),
            ]
        )
    )

    assert tuple(item.category_id for item in result.categories) == SCAN_CATEGORY_ORDER
    selected = {
        item.category_id
        for item in result.categories
        if item.selected
    }
    assert selected == {
        ScanCategoryId.MOD_LANGUAGE,
        ScanCategoryId.PATCHOULI,
    }


def test_selection_all_clear_restore_and_total():
    result = classify_scan_records(
        scan_records(
            [
                record("jar_lang"),
                record("jar_patchouli", key_path="patchouli"),
                record("lang_name", key_path="item.demo"),
                record("future_supported_source", key_path="future"),
            ]
        )
    )
    state = ScanSelectionState.from_result(result)

    assert state.selected_record_count == 2
    assert state.total_record_count == 4
    assert state.select_all().selected_record_count == 4
    assert state.clear().selected_record_count == 0
    assert state.select_all().restore_defaults().selected_record_count == 2
    assert (
        state.set_selected(ScanCategoryId.MOD_LANGUAGE, False)
        .selected_record_count
        == 1
    )


def test_information_categories_cannot_be_selected():
    result = classify_scan_records(
        scan_records(
            [record("jar_lang")],
            diagnostics=[
                (
                    "mods/danger.jar",
                    ZipDiagnostic(
                        code=ENTRY_SIZE_LIMIT,
                        entry="assets/demo/lang/en_us.json",
                        reason="unsafe metadata detail",
                    ),
                ),
                (
                    "mods/broken.jar",
                    ZipDiagnostic(
                        code=BAD_ZIP,
                        entry=None,
                        reason="private exception detail",
                        stops_jar=True,
                    ),
                ),
            ],
        ),
        existing_chinese=ExistingChineseResources(
            status=ChineseResourceStatus.PARTIAL,
            item_count=2,
            source_count=1,
            sources=("mods/chinese.jar",),
        ),
    )
    state = ScanSelectionState.from_result(result)

    for category_id in (
        ScanCategoryId.EXISTING_CHINESE,
        ScanCategoryId.PROTECTED_SKIPPED,
        ScanCategoryId.SAFETY_REJECTED,
        ScanCategoryId.UNREADABLE_SOURCES,
    ):
        summary = next(
            item for item in result.categories if item.category_id is category_id
        )
        assert not summary.translatable
        assert not summary.selected
        assert state.set_selected(category_id, True) is state


def test_scan_diagnostics_do_not_expose_absolute_paths_or_raw_reason():
    private_path = "C:\\Users\\Private\\mods\\danger.jar"
    result = classify_scan_records(
        scan_records(
            [],
            diagnostics=[
                (
                    private_path,
                    ZipDiagnostic(
                        code=ENTRY_SIZE_LIMIT,
                        entry="/private/entry.json",
                        reason="C:\\Users\\Private\\secret",
                    ),
                )
            ],
        )
    )
    serialized = json.dumps(
        [
            {
                "code": item.code,
                "message": item.message,
                "location": item.location,
            }
            for item in result.diagnostics
        ],
        ensure_ascii=False,
    )

    assert "C:\\Users\\Private" not in serialized
    assert "/private/entry.json" not in serialized
    assert result.diagnostics[0].location == ""


def test_scan_service_calls_scanner_once_and_reuses_same_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    modpack = tmp_path / "pack"
    modpack.mkdir()
    scanned = scan_records([record("jar_lang")])
    calls = {"scan": 0}
    observed: dict[str, object] = {}
    original_classifier = scan_service.classify_scan_records

    def fake_scan(path: Path, *, translate_names: bool = False, progress=None):
        calls["scan"] += 1
        assert path == modpack.resolve()
        assert not translate_names
        assert progress is not None
        return scanned

    def capture_classifier(records: ScanRecords, **kwargs):
        observed["classified"] = records
        return original_classifier(records, **kwargs)

    def capture_csv(records: ScanRecords, path: Path):
        observed["csv"] = records

    def capture_report(*, records: ScanRecords, **kwargs):
        observed["report"] = records

    monkeypatch.setattr(scan_service, "scan_modpack", fake_scan)
    monkeypatch.setattr(scan_service, "classify_scan_records", capture_classifier)
    monkeypatch.setattr(scan_service, "write_extracted_csv", capture_csv)
    monkeypatch.setattr(scan_service, "write_scan_report", capture_report)

    result = scan_and_classify(modpack)

    assert calls["scan"] == 1
    assert observed == {
        "classified": scanned,
        "csv": scanned,
        "report": scanned,
    }
    assert result.total_translatable_records == 1


def test_scan_service_emits_controlled_phases_and_writes_real_outputs(
    tmp_path: Path,
):
    modpack = tmp_path / "pack"
    lang_root = modpack / "config" / "ftbquests" / "quests" / "lang" / "en_us"
    lang_root.mkdir(parents=True)
    (lang_root / "chapter.json").write_text(
        json.dumps({"chapter.title": "Getting Started"}),
        encoding="utf-8",
    )
    events = []

    result = scan_and_classify(modpack, progress=events.append)

    assert events[0].stage is ScanProgressStage.PREPARING
    assert any(event.stage is ScanProgressStage.SCANNING for event in events)
    assert tuple(
        event.stage
        for event in events
        if event.stage is not ScanProgressStage.SCANNING
    ) == (
        ScanProgressStage.PREPARING,
        ScanProgressStage.CLASSIFYING,
        ScanProgressStage.WRITING,
        ScanProgressStage.COMPLETED,
    )
    assert result.total_translatable_records == 1
    assert (modpack / ".mc-han" / "extracted_texts.csv").is_file()
    assert (modpack / ".mc-han" / "logs" / "scan_report.txt").is_file()


def test_scan_service_rescan_preserves_translation_notes_and_statuses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    modpack = tmp_path / "pack"
    modpack.mkdir()
    paths = project_paths(modpack)
    original_records = [
        replace(
            record("jar_lang", key_path="translated"),
            translation="已有译文",
        ),
        replace(
            record("jar_lang", key_path="edited"),
            translation="人工编辑译文",
            note="edited",
        ),
        replace(
            record("jar_lang", key_path="reviewed"),
            translation="审核译文",
            note="review:ok",
        ),
        replace(
            record("jar_lang", key_path="retry"),
            note="needs_retranslate",
        ),
        replace(
            record("jar_lang", key_path="skipped"),
            note="skip",
        ),
        replace(
            record("jar_lang", key_path="structured-review"),
            review_status="approved",
        ),
        replace(
            record("jar_lang", key_path="structured-skip"),
            skip_status="user_skipped",
        ),
    ]
    write_extracted_csv(original_records, paths.extracted_csv)
    scanned = scan_records(
        [
            record("jar_lang", key_path=item.key_path)
            for item in original_records
        ]
    )
    monkeypatch.setattr(scan_service, "scan_modpack", lambda *args, **kwargs: scanned)
    monkeypatch.setattr(scan_service, "write_scan_report", lambda **kwargs: None)

    scan_and_classify(modpack)

    actual = {item.key_path: item for item in read_extracted_csv(paths.extracted_csv)}
    assert actual["translated"].translation == "已有译文"
    assert actual["edited"].translation == "人工编辑译文"
    assert actual["edited"].note == "edited"
    assert actual["reviewed"].note == "review:ok"
    assert actual["retry"].note == "needs_retranslate"
    assert actual["skipped"].note == "skip"
    assert actual["structured-review"].review_status == "approved"
    assert actual["structured-skip"].skip_status == "user_skipped"


def test_scan_service_rescan_handles_add_delete_change_and_context_isolation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    modpack = tmp_path / "pack"
    modpack.mkdir()
    paths = project_paths(modpack)
    old_records = [
        replace(
            record("jar_lang", key_path="keep", original="Keep"),
            translation="保留",
            note="review:ok",
            review_status="approved",
        ),
        replace(
            record("jar_lang", key_path="removed", original="Removed"),
            translation="已删除",
        ),
        replace(
            record("jar_lang", key_path="changed", original="Before"),
            translation="旧译文",
            note="review:ok",
            review_status="approved",
        ),
        replace(
            record("jar_lang", key_path="context-a", original="Shared"),
            translation="上下文 A",
            note="edited",
        ),
    ]
    write_extracted_csv(old_records, paths.extracted_csv)
    scanned = scan_records(
        [
            record("jar_lang", key_path="keep", original="Keep"),
            record("jar_lang", key_path="changed", original="After"),
            record("jar_lang", key_path="context-b", original="Shared"),
            record("jar_lang", key_path="new", original="New"),
        ]
    )
    monkeypatch.setattr(scan_service, "scan_modpack", lambda *args, **kwargs: scanned)
    monkeypatch.setattr(scan_service, "write_scan_report", lambda **kwargs: None)

    scan_and_classify(modpack)

    actual = {item.key_path: item for item in read_extracted_csv(paths.extracted_csv)}
    assert set(actual) == {"changed", "context-b", "keep", "new"}
    assert actual["keep"].translation == "保留"
    assert actual["keep"].review_status == "approved"
    for key in ("changed", "context-b", "new"):
        assert actual[key].translation == ""
        assert actual[key].note == ""
        assert actual[key].review_status == ""
        assert actual[key].skip_status == ""


def test_scan_service_scanner_failure_does_not_modify_existing_csv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    modpack = tmp_path / "pack"
    modpack.mkdir()
    paths = project_paths(modpack)
    write_extracted_csv(
        [replace(record("jar_lang"), translation="重要译文")],
        paths.extracted_csv,
    )
    before = paths.extracted_csv.read_bytes()

    def fail_scan(*args, **kwargs):
        raise OSError("simulated scan failure")

    monkeypatch.setattr(scan_service, "scan_modpack", fail_scan)

    with pytest.raises(scan_service.ScanServiceError, match="扫描未能完成"):
        scan_and_classify(modpack)

    assert paths.extracted_csv.read_bytes() == before


def test_scan_service_atomic_save_failure_reports_preserved_old_csv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    modpack = tmp_path / "pack"
    modpack.mkdir()
    paths = project_paths(modpack)
    write_extracted_csv(
        [replace(record("jar_lang"), translation="重要译文")],
        paths.extracted_csv,
    )
    before = paths.extracted_csv.read_bytes()
    monkeypatch.setattr(
        scan_service,
        "scan_modpack",
        lambda *args, **kwargs: scan_records([record("jar_lang")]),
    )

    def fail_replace(source: Path, destination: Path) -> None:
        raise PermissionError("simulated replace failure")

    monkeypatch.setattr(csv_store.os, "replace", fail_replace)

    with pytest.raises(scan_service.ScanServiceError) as captured:
        scan_and_classify(modpack)

    assert captured.value.code == "scan_save_failed"
    assert "原有清单已经保留" in captured.value.message
    assert not captured.value.partial_saved
    assert paths.extracted_csv.read_bytes() == before
    assert not list(paths.state_dir.glob(".mc-han-csv-*.tmp"))


def test_scan_service_rejects_unknown_nonempty_column_without_scanning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    modpack = tmp_path / "pack"
    modpack.mkdir()
    paths = project_paths(modpack)
    paths.state_dir.mkdir()
    fields = [*record("jar_lang").to_csv_row(), "future_status"]
    with paths.extracted_csv.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerow(
            {
                **record("jar_lang").to_csv_row(),
                "future_status": "must-not-be-lost",
            }
        )
    before = paths.extracted_csv.read_bytes()
    scan_called = False

    def fake_scan(*args, **kwargs):
        nonlocal scan_called
        scan_called = True
        return scan_records([])

    monkeypatch.setattr(scan_service, "scan_modpack", fake_scan)

    with pytest.raises(scan_service.ScanServiceError) as captured:
        scan_and_classify(modpack)

    assert captured.value.code == "unsupported_csv_schema"
    assert not scan_called
    assert paths.extracted_csv.read_bytes() == before


def test_selection_can_filter_records_without_reimplementing_category_rules():
    records = [
        record("jar_lang", key_path="one"),
        record("lang_name", key_path="two"),
        record("future_supported_source", key_path="three"),
        record("jar_patchouli", key_path="four"),
    ]
    result = classify_scan_records(scan_records(records))
    defaults = ScanSelectionState.from_result(result)

    assert category_for_record(records[2]) is ScanCategoryId.OTHER_SUPPORTED
    assert defaults.is_record_selected(records[0])
    assert not defaults.is_record_selected(records[1])
    assert not defaults.is_record_selected(records[2])
    assert defaults.selected_records(records) == (records[0], records[3])
    assert defaults.select_all().selected_records(records) == tuple(records)
    assert defaults.clear().selected_records(records) == ()
    assert defaults.select_all().restore_defaults().selected_records(records) == (
        records[0],
        records[3],
    )
