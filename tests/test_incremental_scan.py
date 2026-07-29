from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from mc_han.core.project import project_paths
from mc_han.csv_store import read_extracted_csv
from mc_han.models import ExtractedText
from mc_han.qt.scan_view_models import ScanPageViewModel
from mc_han.scanner import ScanRecords
from mc_han.services.incremental_scan import reconcile_incremental_scan
from mc_han.services.provenance import TranslationProvenanceStore
from mc_han.services.scan_service import classify_scan_records, scan_and_classify
from mc_han.services.translation_review import (
    ReviewAction,
    update_translation_review_record,
)
from mc_han.services.translation_rules import TranslationRuleStore
from mc_han.workflow.incremental import ScanChangeStatus
from mc_han.workflow.provenance import TranslationSource
from mc_han.workflow.scan_models import ScanSelectionState
from mc_han.workflow.translation_rules import (
    TranslationRuleScope,
    TranslationRuleType,
)


def _record(
    record_id: str,
    *,
    container: str = "mods/demo-1.0.jar",
    file_path: str = "assets/demo/lang/en_us.json",
    key_path: str = "demo.text",
    original: str = "Original text",
    translation: str = "",
    note: str = "",
    review_status: str = "",
) -> ExtractedText:
    return ExtractedText(
        id=record_id,
        source_type="jar_lang",
        container=container,
        file_path=file_path,
        key_path=key_path,
        original=original,
        translation=translation,
        note=note,
        review_status=review_status,
    )


def test_incremental_scan_classifies_all_stable_statuses():
    unchanged = _record("unchanged")
    source_old = _record("source-old", key_path="demo.changed")
    source_new = _record(
        "source-new",
        key_path="demo.changed",
        original="Updated English",
    )
    moved_old = _record("moved-old", key_path="demo.moved")
    moved_new = _record(
        "moved-new",
        container="mods/demo-1.1.jar",
        key_path="demo.moved",
    )
    removed = _record("removed", key_path="demo.removed")
    added = _record("added", key_path="demo.added")

    result = reconcile_incremental_scan(
        [unchanged, source_new, moved_new, added],
        [unchanged, source_old, moved_old, removed],
    )

    assert result.summary.count(ScanChangeStatus.UNCHANGED) == 1
    assert result.summary.count(ScanChangeStatus.SOURCE_CHANGED) == 1
    assert result.summary.count(ScanChangeStatus.CONTEXT_CHANGED) == 1
    assert result.summary.count(ScanChangeStatus.ADDED) == 1
    assert result.summary.count(ScanChangeStatus.REMOVED) == 1
    assert result.summary.migrated_manual_count == 0


def test_only_manual_or_confirmed_work_migrates_to_changed_source(
    tmp_path: Path,
):
    old_manual = _record(
        "old-manual",
        translation="人工译文",
        note="edited",
    )
    new_manual = _record("new-manual", original="Changed English")
    old_ai = _record(
        "old-ai",
        key_path="demo.ai",
        translation="AI 译文",
    )
    new_ai = _record(
        "new-ai",
        key_path="demo.ai",
        original="Changed AI English",
    )
    store_path = tmp_path / "provenance.sqlite3"
    with TranslationProvenanceStore(store_path) as store:
        store.record_translation(
            old_manual,
            old_manual.translation,
            source=TranslationSource.MANUAL,
        )
        store.record_translation(
            old_ai,
            old_ai.translation,
            source=TranslationSource.AI,
        )
        result = reconcile_incremental_scan(
            [new_manual, new_ai],
            [old_manual, old_ai],
            provenance_store=store,
        )

    current = {record.id: record for record in result.records}
    assert current["new-manual"].translation == "人工译文"
    assert current["new-manual"].review_status == "needs_retranslate"
    assert current["new-ai"].translation == ""
    assert result.summary.migrated_manual_count == 1


def test_incremental_scan_reports_rules_whose_targets_changed(
    tmp_path: Path,
):
    old = _record("old", translation="人工译文", note="edited")
    new = _record(
        "new",
        container="mods/demo-1.1.jar",
    )
    store = TranslationRuleStore(tmp_path / "rules.json")
    record_rule = store.add_feedback_rule(
        old,
        rule_type=TranslationRuleType.EXACT,
        scope=TranslationRuleScope.RECORD,
        instruction="固定译法",
        source="test",
    )
    file_rule = store.add_feedback_rule(
        old,
        rule_type=TranslationRuleType.STYLE,
        scope=TranslationRuleScope.FILE,
        instruction="简洁",
        source="test",
    )

    result = reconcile_incremental_scan(
        [new],
        [old],
        rules=store.load(),
    )

    assert result.summary.context_changed_count == 1
    assert set(result.summary.affected_rule_ids) == {
        record_rule.rule_id,
        file_rule.rule_id,
    }
    assert len(store.load()) == 2


def test_confirmed_translation_is_preserved_for_review_after_update(
    tmp_path: Path,
):
    previous = _record("old", translation="已确认译文")
    current = _record("new", original="Updated English")
    store_path = tmp_path / "provenance.sqlite3"
    with TranslationProvenanceStore(store_path) as store:
        store.record_translation(
            previous,
            previous.translation,
            source=TranslationSource.AI,
        )
        confirmed = store.confirm_manual(previous)
        reconciliation = reconcile_incremental_scan(
            [current],
            [previous],
            provenance_store=store,
        )
        migration = reconciliation.migrations[0]
        migrated = store.migrate_translation_after_update(
            migration.previous_provenance,
            migration.current,
        )

    assert reconciliation.records[0].translation == "已确认译文"
    assert reconciliation.records[0].review_status == "needs_retranslate"
    assert migrated.current_source is TranslationSource.AI
    assert migrated.manual_confirmed_at == confirmed.manual_confirmed_at
    assert migrated.changed_after_update


def test_real_rescan_preserves_manual_work_writes_diff_and_marks_provenance(
    tmp_path: Path,
):
    modpack = tmp_path / "pack"
    lang = modpack / "kubejs" / "assets" / "demo" / "lang"
    lang.mkdir(parents=True)
    en_path = lang / "en_us.json"
    en_path.write_text(
        json.dumps({"screen.demo.title": "Original title"}),
        encoding="utf-8",
    )
    first_result = scan_and_classify(modpack)
    paths = project_paths(modpack)
    first = read_extracted_csv(paths.extracted_csv)[0]
    update_translation_review_record(
        paths.extracted_csv,
        first.id,
        ReviewAction.EDIT,
        translation="人工标题",
        provenance_path=paths.provenance_sqlite,
    )
    update_translation_review_record(
        paths.extracted_csv,
        first.id,
        ReviewAction.APPROVE,
        provenance_path=paths.provenance_sqlite,
    )
    rule_store = TranslationRuleStore(paths.translation_rules_json)
    rule = rule_store.add_feedback_rule(
        first,
        rule_type=TranslationRuleType.EXACT,
        scope=TranslationRuleScope.RECORD,
        instruction="使用人工标题",
        source="test",
    )

    en_path.write_text(
        json.dumps({"screen.demo.title": "Updated title"}),
        encoding="utf-8",
    )
    result = scan_and_classify(modpack)
    updated = read_extracted_csv(paths.extracted_csv)[0]

    assert first_result.incremental_summary.added_count == 1
    assert result.incremental_summary.source_changed_count == 1
    assert result.incremental_summary.migrated_manual_count == 1
    assert result.incremental_summary.affected_rule_ids == (rule.rule_id,)
    assert updated.translation == "人工标题"
    assert updated.review_status == "needs_retranslate"
    assert paths.scan_diff_json.is_file()
    payload = json.loads(paths.scan_diff_json.read_text(encoding="utf-8"))
    assert payload["counts"]["source_changed"] == 1
    with TranslationProvenanceStore(paths.provenance_sqlite) as store:
        provenance = store.get(updated.id)
    assert provenance is not None
    assert provenance.current_source is TranslationSource.MANUAL
    assert provenance.changed_after_update
    assert provenance.manual_confirmed_at
    assert len(rule_store.load()) == 1


def test_incremental_summary_is_visible_in_scan_page_view_model():
    previous = _record("old", translation="人工译文", note="edited")
    current = _record("new", original="Updated English")
    reconciliation = reconcile_incremental_scan([current], [previous])
    records = ScanRecords(list(reconciliation.records), inventory={})
    result = classify_scan_records(
        records,
        incremental_summary=reconciliation.summary,
    )
    view_model = ScanPageViewModel.from_selection(
        "Demo",
        ScanSelectionState.from_result(result),
    )

    assert "需要复核" in view_model.incremental_title
    assert "英文变化 1 条" in view_model.incremental_detail
