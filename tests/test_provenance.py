from __future__ import annotations

import json
import zipfile
from dataclasses import replace
from pathlib import Path

from mc_han.core.project import project_paths
from mc_han.csv_store import read_extracted_csv, write_extracted_csv
from mc_han.models import ExtractedText
from mc_han.scanner import ScanRecords, scan_modpack
from mc_han.services.provenance import (
    TranslationProvenanceStore,
    choose_existing_candidate,
)
from mc_han.services.scan_service import scan_and_classify
from mc_han.services.translation_planning import build_translation_plan_comparison
from mc_han.services.translation_review import (
    ReviewAction,
    update_translation_review_record,
)
from mc_han.translator.engine import translate_csv
from mc_han.translator.mock_provider import MockTranslator
from mc_han.workflow.provenance import (
    ExistingTranslationCandidate,
    TranslationSource,
    original_text_hash,
)
from mc_han.workflow.scan_models import ScanSelectionState
from mc_han.workflow.translation_plan import TranslationPlanMode


def _record(
    *,
    translation: str = "",
    original: str = "Open the terminal",
) -> ExtractedText:
    return ExtractedText(
        id="record",
        source_type="jar_lang",
        container="mods/demo-1.0.jar",
        file_path="assets/demo/lang/en_us.json",
        key_path="screen.demo.title",
        original=original,
        translation=translation,
    )


def _candidate(
    record: ExtractedText,
    source: TranslationSource,
    *,
    translation: str,
    artifact_version: str = "artifact-v1",
) -> ExistingTranslationCandidate:
    return ExistingTranslationCandidate(
        record_id=record.id,
        source=source,
        translation=translation,
        mod_id="demo",
        key_path=record.key_path,
        original_hash=original_text_hash(record.original),
        artifact_version=artifact_version,
        source_location="assets/demo/lang/zh_cn.json",
    )


def _localized_modpack(tmp_path: Path) -> Path:
    modpack = tmp_path / "pack"
    mods = modpack / "mods"
    mods.mkdir(parents=True)
    with zipfile.ZipFile(mods / "demo-1.0.jar", "w") as jar:
        jar.writestr(
            "assets/demo/lang/en_us.json",
            json.dumps(
                {
                    "screen.demo.title": "Open the terminal",
                    "screen.demo.help": "Read the guide",
                }
            ),
        )
        jar.writestr(
            "assets/demo/lang/zh_cn.json",
            json.dumps(
                {
                    "screen.demo.title": "打开终端",
                    "screen.demo.other": "不应串用",
                }
            ),
        )
    return modpack


def test_existing_candidate_priority_and_identity_match_are_stable():
    record = _record()
    official = _candidate(
        record,
        TranslationSource.OFFICIAL_ZH_CN,
        translation="官方译文",
    )
    author = _candidate(
        record,
        TranslationSource.MODPACK_AUTHOR,
        translation="作者译文",
    )

    selected = choose_existing_candidate(
        record,
        (official, author),
        artifact_version="artifact-v1",
        mod_id="demo",
    )

    assert selected == author
    assert (
        choose_existing_candidate(
            record,
            (official,),
            artifact_version="artifact-v2",
            mod_id="demo",
        )
        is None
    )
    assert (
        choose_existing_candidate(
            replace(record, original="Changed"),
            (official,),
            artifact_version="artifact-v1",
            mod_id="demo",
        )
        is None
    )


def test_provenance_store_tracks_initial_current_and_manual_history(
    tmp_path: Path,
):
    path = tmp_path / "provenance.sqlite3"
    record = _record(translation="打开终端")
    with TranslationProvenanceStore(path) as store:
        official = store.record_translation(
            record,
            record.translation,
            source=TranslationSource.OFFICIAL_ZH_CN,
            artifact_version="artifact-v1",
            mod_id="demo",
        )
        manual_record = replace(record, translation="开启终端")
        manual = store.record_translation(
            manual_record,
            manual_record.translation,
            source=TranslationSource.MANUAL,
            artifact_version="artifact-v1",
            mod_id="demo",
        )
        confirmed = store.confirm_manual(manual_record)

    assert official.initial_source is TranslationSource.OFFICIAL_ZH_CN
    assert manual.initial_source is TranslationSource.OFFICIAL_ZH_CN
    assert manual.current_source is TranslationSource.MANUAL
    assert confirmed.manual_confirmed_at
    assert confirmed.translation_hash != official.translation_hash


def test_scan_reuses_only_matching_official_zh_cn_in_same_jar_pass(
    tmp_path: Path,
):
    modpack = _localized_modpack(tmp_path)

    records = scan_modpack(modpack)
    by_key = {record.key_path: record for record in records}

    assert by_key["screen.demo.title"].translation == "打开终端"
    assert by_key["screen.demo.title"].note == "source:official_zh_cn"
    assert by_key["screen.demo.help"].translation == ""
    assert len(records.provenance_candidates) == 1
    candidate = records.provenance_candidates[0]
    assert candidate.original_hash == original_text_hash("Open the terminal")
    assert candidate.mod_id == "demo"
    assert len(candidate.artifact_version) == 64


def test_scan_service_persists_official_source_and_plan_counts_reuse(
    tmp_path: Path,
):
    modpack = _localized_modpack(tmp_path)

    result = scan_and_classify(modpack)
    paths = project_paths(modpack)
    records = read_extracted_csv(paths.extracted_csv)
    localized = next(
        record for record in records if record.key_path == "screen.demo.title"
    )
    with TranslationProvenanceStore(paths.provenance_sqlite) as store:
        provenance = store.get(localized.id)
    assert provenance is not None
    assert provenance.current_source is TranslationSource.OFFICIAL_ZH_CN

    selection = ScanSelectionState.from_result(result).select_all()
    plan = build_translation_plan_comparison(
        records,
        selection,
        provider="deepseek",
        base_model="deepseek-v4-flash",
        provenance_path=paths.provenance_sqlite,
    ).for_mode(TranslationPlanMode.BALANCED)
    assert plan.existing_chinese_reuse_count == 1
    assert plan.historical_translation_count == 0
    assert plan.ai_translation_count == 1


def test_ftbquests_paired_language_reuses_modpack_author_translation(
    tmp_path: Path,
):
    modpack = tmp_path / "pack"
    lang = modpack / "config" / "ftbquests" / "quests" / "lang"
    lang.mkdir(parents=True)
    (lang / "en_us.json").write_text(
        json.dumps({"chapter.start": "Getting Started"}),
        encoding="utf-8",
    )
    (lang / "zh_cn.json").write_text(
        json.dumps({"chapter.start": "入门"}),
        encoding="utf-8",
    )

    scan_and_classify(modpack)
    paths = project_paths(modpack)
    record = read_extracted_csv(paths.extracted_csv)[0]
    assert record.translation == "入门"
    with TranslationProvenanceStore(paths.provenance_sqlite) as store:
        provenance = store.get(record.id)
    assert provenance is not None
    assert provenance.current_source is TranslationSource.MODPACK_AUTHOR


def test_existing_project_translation_keeps_priority_over_new_official_text(
    tmp_path: Path,
):
    modpack = _localized_modpack(tmp_path)
    paths = project_paths(modpack)
    first = scan_modpack(modpack)
    edited = [
        replace(record, translation="人工项目译文", note="edited")
        if record.key_path == "screen.demo.title"
        else record
        for record in first
    ]
    write_extracted_csv(edited, paths.extracted_csv)

    scan_and_classify(modpack)

    saved = {
        record.key_path: record for record in read_extracted_csv(paths.extracted_csv)
    }
    assert saved["screen.demo.title"].translation == "人工项目译文"
    with TranslationProvenanceStore(paths.provenance_sqlite) as store:
        provenance = store.get(saved["screen.demo.title"].id)
    assert provenance is not None
    assert provenance.current_source is TranslationSource.PROJECT_HISTORY


def test_translation_engine_and_review_update_provenance_without_real_api(
    tmp_path: Path,
):
    csv_path = tmp_path / ".mc-han" / "extracted_texts.csv"
    provenance_path = tmp_path / ".mc-han" / "provenance.sqlite3"
    record = _record()
    write_extracted_csv([record], csv_path)

    translated, _, _ = translate_csv(
        input_csv=csv_path,
        output_csv=csv_path,
        translator=MockTranslator(),
        cache_path=tmp_path / ".mc-han" / "cache.jsonl",
        provenance_path=provenance_path,
        rule_version="rules-v1",
    )
    with TranslationProvenanceStore(provenance_path) as store:
        ai = store.get(record.id)
    assert ai is not None
    assert ai.current_source is TranslationSource.AI
    assert ai.provider == MockTranslator.provider_name
    assert ai.model == MockTranslator.model
    assert ai.rule_version == "rules-v1"

    update_translation_review_record(
        csv_path,
        record.id,
        ReviewAction.EDIT,
        translation="人工译文",
        provenance_path=provenance_path,
    )
    update_translation_review_record(
        csv_path,
        record.id,
        ReviewAction.APPROVE,
        provenance_path=provenance_path,
    )
    with TranslationProvenanceStore(provenance_path) as store:
        manual = store.get(record.id)
    assert manual is not None
    assert manual.initial_source is TranslationSource.AI
    assert manual.current_source is TranslationSource.MANUAL
    assert manual.manual_confirmed_at
    assert translated[0].translation
