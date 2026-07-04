from __future__ import annotations

import json
from pathlib import Path

from mc_han.builder.resourcepack import build_complete_install_package, build_outputs
from mc_han.csv_store import read_extracted_csv, write_extracted_csv
from mc_han.models import ExtractedText
from mc_han.quality.checks import check_csv
from mc_han.scanner import merge_existing_translations, scan_modpack
from mc_han.translator.engine import TranslationItemCompleted, TranslationStarted, translate_csv
from mc_han.translator.mock_provider import MockTranslator

from test_scanner import create_sample_modpack


def test_translate_names_scan_extracts_display_names_as_lang_name(tmp_path: Path):
    modpack = create_sample_modpack(tmp_path)

    default_records = scan_modpack(modpack)
    name_records = scan_modpack(modpack, translate_names=True)

    assert "Storm Machine" not in [record.original for record in default_records]
    assert "Storm Machine" in [record.original for record in name_records]
    assert any(record.source_type == "lang_name" and record.key_path.startswith("item.") for record in name_records)
    assert any(record.source_type == "lang_name" and record.key_path.startswith("block.") for record in name_records)
    assert any(record.source_type == "lang_name" and record.key_path.startswith("entity.") for record in name_records)
    assert not any(record.source_type == "lang_name" and record.container == "modpack" and record.file_path.startswith("config/") for record in name_records)


def test_rescan_with_translate_names_preserves_existing_translations_and_adds_names(tmp_path: Path):
    modpack = create_sample_modpack(tmp_path)
    first_records = scan_modpack(modpack, translate_names=False)
    translated_first = [
        record.__class__(
            id=record.id,
            source_type=record.source_type,
            container=record.container,
            file_path=record.file_path,
            key_path=record.key_path,
            original=record.original,
            translation="已有译文" if record.original == "Welcome to the quest book." else record.translation,
            note=record.note,
        )
        for record in first_records
    ]

    rescanned = merge_existing_translations(scan_modpack(modpack, translate_names=True), translated_first)

    assert any(record.source_type == "lang_name" for record in rescanned)
    assert any(record.original == "Storm Machine" for record in rescanned)
    assert any(record.original == "Welcome to the quest book." and record.translation == "已有译文" for record in rescanned)


def test_name_translation_keeps_english_original_and_emits_events(tmp_path: Path):
    csv_path = tmp_path / "names.csv"
    output_csv = tmp_path / "translated.csv"
    write_extracted_csv(
        [
            ExtractedText(
                id="name-1",
                source_type="lang_name",
                container="mods/demo.jar",
                file_path="assets/demo/lang/en_us.json",
                key_path="item.demo.wrench",
                original="Wrench",
            )
        ],
        csv_path,
    )
    events: list[object] = []

    records, translated_count, cache_hits = translate_csv(
        input_csv=csv_path,
        output_csv=output_csv,
        translator=MockTranslator(),
        cache_path=tmp_path / "cache.jsonl",
        event_callback=events.append,
    )

    assert translated_count == 1
    assert cache_hits == 0
    assert records[0].translation == "扳手 (Wrench)"
    assert any(isinstance(event, TranslationStarted) for event in events)
    assert any(isinstance(event, TranslationItemCompleted) for event in events)
    assert not check_csv(output_csv)


def test_quality_flags_name_translation_without_english_original(tmp_path: Path):
    csv_path = tmp_path / "bad_names.csv"
    write_extracted_csv(
        [
            ExtractedText(
                id="name-1",
                source_type="lang_name",
                container="mods/demo.jar",
                file_path="assets/demo/lang/en_us.json",
                key_path="block.demo.crusher",
                original="The Crusher",
                translation="粉碎机",
            )
        ],
        csv_path,
    )

    issues = check_csv(csv_path)

    assert any(issue.code == "name_english_original_missing" for issue in issues)


def test_name_rows_build_only_client_resourcepack_and_complete_readme_notes(tmp_path: Path):
    modpack = create_sample_modpack(tmp_path)
    csv_path = tmp_path / "translated.csv"
    output_dir = tmp_path / "build"
    write_extracted_csv(
        [
            ExtractedText(
                id="name-1",
                source_type="lang_name",
                container="mods/demo.jar",
                file_path="assets/demo/lang/en_us.json",
                key_path="item.demo.wrench",
                original="Wrench",
                translation="扳手 (Wrench)",
            )
        ],
        csv_path,
    )

    stats = build_outputs(modpack_dir=modpack, csv_path=csv_path, output_dir=output_dir)
    complete_root = build_complete_install_package(output_dir=output_dir, translate_names=True)
    lang_path = output_dir / "resourcepacks" / "mc-han-cn" / "assets" / "demo" / "lang" / "zh_cn.json"

    assert stats["name_rows"] == 1
    assert json.loads(lang_path.read_text(encoding="utf-8"))["item.demo.wrench"] == "扳手 (Wrench)"
    assert not (output_dir / "config").exists()
    assert "物品/方块名称翻译" in (complete_root / "README_ALL.txt").read_text(encoding="utf-8")
