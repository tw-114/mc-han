from __future__ import annotations

import json

from mc_han.builder.resourcepack import (
    build_client_resourcepack,
    build_complete_install_package,
    build_outputs,
    build_server_pack,
)
from mc_han.builder.installer import install_outputs, plan_install_outputs, rollback_install, write_install_plan_report
from mc_han.csv_store import read_extracted_csv, write_extracted_csv
from mc_han.models import ExtractedText
from mc_han.quality.checks import check_csv, check_output_dir
from mc_han.scanner import scan_modpack
from mc_han.translator.engine import translate_csv
from mc_han.translator.mock_provider import MockTranslator

from test_scanner import create_sample_modpack


def test_mock_translate_updates_csv_and_cache(tmp_path):
    modpack = create_sample_modpack(tmp_path)
    input_csv = tmp_path / "extracted_texts.csv"
    output_csv = tmp_path / "translated.csv"
    cache_path = tmp_path / "translation_cache.jsonl"
    write_extracted_csv(scan_modpack(modpack), input_csv)

    records, translated_count, cache_hits = translate_csv(
        input_csv=input_csv,
        output_csv=output_csv,
        translator=MockTranslator(),
        cache_path=cache_path,
    )

    assert translated_count == len(records)
    assert cache_hits == 0
    assert all(record.translation for record in read_extracted_csv(output_csv))
    assert cache_path.exists()
    assert "API" not in cache_path.read_text(encoding="utf-8")


def test_build_outputs_resourcepack_and_ftb_overlay(tmp_path):
    modpack = create_sample_modpack(tmp_path)
    csv_path = tmp_path / "translated.csv"
    output_dir = tmp_path / "build"
    records, _translated_count, _cache_hits = translate_csv(
        input_csv=write_scan_csv(tmp_path, modpack),
        output_csv=csv_path,
        translator=MockTranslator(),
        cache_path=tmp_path / "cache.jsonl",
    )

    stats = build_outputs(modpack_dir=modpack, csv_path=csv_path, output_dir=output_dir)

    assert stats["translated_rows"] == len(records)
    assert stats["resource_files"] >= 3
    assert stats["config_files"] >= 1
    patchouli_path = (
        output_dir
        / "resourcepacks"
        / "mc-han-cn"
        / "assets"
        / "demo"
        / "patchouli_books"
        / "book"
        / "zh_cn"
        / "entries"
        / "intro.json"
    )
    assert patchouli_path.exists()
    assert json.loads(patchouli_path.read_text(encoding="utf-8"))
    assert "模拟译文" in patchouli_path.read_text(encoding="utf-8")
    lang_path = output_dir / "resourcepacks" / "mc-han-cn" / "assets" / "demo" / "lang" / "zh_cn.json"
    lang_data = json.loads(lang_path.read_text(encoding="utf-8"))
    assert "tooltip.demo.machine" in lang_data
    assert "message.demo.warning" in lang_data
    assert "screen.demo.title" in lang_data
    assert "item.demo.machine" not in lang_data
    assert "entity.demo.beast" not in lang_data
    ftb_overlay = output_dir / "config" / "ftbquests" / "quests" / "chapters" / "chapter_1.snbt"
    assert ftb_overlay.exists()
    assert "模拟译文" in ftb_overlay.read_text(encoding="utf-8")
    assert not check_output_dir(output_dir)


def test_build_client_and_server_packs_are_split(tmp_path):
    modpack = create_sample_modpack(tmp_path)
    csv_path = tmp_path / "translated.csv"
    output_dir = tmp_path / "output"
    translate_csv(
        input_csv=write_scan_csv(tmp_path, modpack),
        output_csv=csv_path,
        translator=MockTranslator(),
        cache_path=tmp_path / "cache.jsonl",
    )

    client_stats = build_client_resourcepack(modpack_dir=modpack, csv_path=csv_path, output_dir=output_dir)
    server_stats = build_server_pack(modpack_dir=modpack, csv_path=csv_path, output_dir=output_dir)

    client_root = output_dir / "mc-han-client-resourcepack"
    server_root = output_dir / "mc-han-server-pack"
    assert client_stats["resource_files"] >= 1
    assert client_stats["config_files"] == 0
    assert server_stats["resource_files"] == 0
    assert server_stats["config_files"] >= 1
    assert (client_root / "pack.mcmeta").exists()
    assert (client_root / "README_CLIENT.txt").exists()
    assert not (client_root / "config").exists()
    assert (server_root / "config" / "ftbquests" / "quests").exists()
    assert (server_root / "README_SERVER.txt").exists()
    assert not (server_root / "resourcepacks").exists()


def test_build_complete_install_package_contains_ascii_bat(tmp_path):
    output_dir = tmp_path / "output"
    (output_dir / "config" / "ftbquests" / "quests").mkdir(parents=True)
    (output_dir / "config" / "ftbquests" / "quests" / "chapter.snbt").write_text("title: \"测试\"", encoding="utf-8")
    (output_dir / "resourcepacks" / "mc-han-cn").mkdir(parents=True)
    (output_dir / "resourcepacks" / "mc-han-cn" / "pack.mcmeta").write_text("{}", encoding="utf-8")

    complete_root = build_complete_install_package(output_dir=output_dir)
    bat_bytes = (complete_root / "install_cn_pack.bat").read_bytes()

    bat_bytes.decode("ascii")
    assert (complete_root / "config" / "ftbquests" / "quests" / "chapter.snbt").exists()
    assert (complete_root / "resourcepacks" / "mc-han-cn" / "pack.mcmeta").exists()
    assert (complete_root / "README_ALL.txt").exists()


def test_check_csv_flags_placeholder_mismatch(tmp_path):
    csv_path = tmp_path / "bad.csv"
    write_extracted_csv(
        [
            ExtractedText(
                id="1",
                source_type="jar_patchouli",
                container="mods/demo.jar",
                file_path="assets/demo/patchouli_books/book/en_us/entries/intro.json",
                key_path="pages[0].text",
                original="Use %s and $(item)Quartz$(0).",
                translation="使用石英。",
            )
        ],
        csv_path,
    )

    issues = check_csv(csv_path)

    assert any(issue.code == "placeholder_mismatch" for issue in issues)


def test_install_outputs_copies_files_and_backs_up_existing_targets(tmp_path):
    modpack = create_sample_modpack(tmp_path)
    csv_path = tmp_path / "translated.csv"
    output_dir = tmp_path / "build"
    translate_csv(
        input_csv=write_scan_csv(tmp_path, modpack),
        output_csv=csv_path,
        translator=MockTranslator(),
        cache_path=tmp_path / "cache.jsonl",
    )
    build_outputs(modpack_dir=modpack, csv_path=csv_path, output_dir=output_dir)

    first = install_outputs(modpack_dir=modpack, build_dir=output_dir)
    second = install_outputs(modpack_dir=modpack, build_dir=output_dir)

    assert first.installed_files > 0
    assert first.backed_up_files > 0
    assert second.installed_files > 0
    assert second.backed_up_files > 0
    assert (modpack / "resourcepacks" / "mc-han-cn" / "pack.mcmeta").exists()
    assert second.backup_dir.exists()
    assert (output_dir / "install_report.txt").exists()


def test_install_dry_run_writes_plan_without_copying(tmp_path):
    modpack = create_sample_modpack(tmp_path)
    csv_path = tmp_path / "translated.csv"
    output_dir = tmp_path / "build"
    translate_csv(
        input_csv=write_scan_csv(tmp_path, modpack),
        output_csv=csv_path,
        translator=MockTranslator(),
        cache_path=tmp_path / "cache.jsonl",
    )
    build_outputs(modpack_dir=modpack, csv_path=csv_path, output_dir=output_dir)

    plan = plan_install_outputs(modpack_dir=modpack, build_dir=output_dir)
    report_path = output_dir / "install_plan.txt"
    write_install_plan_report(plan, report_path)

    assert plan.total_files > 0
    assert plan.overwrite_files > 0
    assert any(item.relative_target == "resourcepacks/mc-han-cn/pack.mcmeta" for item in plan.items)
    assert report_path.exists()
    assert not (modpack / "resourcepacks" / "mc-han-cn" / "pack.mcmeta").exists()


def test_install_rollback_restores_previous_files_and_removes_new_files(tmp_path):
    modpack = create_sample_modpack(tmp_path)
    original_chapter = modpack / "config" / "ftbquests" / "quests" / "chapters" / "chapter_1.snbt"
    original_text = original_chapter.read_text(encoding="utf-8")
    csv_path = tmp_path / "translated.csv"
    output_dir = tmp_path / "build"
    translate_csv(
        input_csv=write_scan_csv(tmp_path, modpack),
        output_csv=csv_path,
        translator=MockTranslator(),
        cache_path=tmp_path / "cache.jsonl",
    )
    build_outputs(modpack_dir=modpack, csv_path=csv_path, output_dir=output_dir)

    install_result = install_outputs(modpack_dir=modpack, build_dir=output_dir)
    assert install_result.manifest_path and install_result.manifest_path.exists()
    assert "模拟译文" in original_chapter.read_text(encoding="utf-8")
    assert (modpack / "resourcepacks" / "mc-han-cn" / "pack.mcmeta").exists()

    rollback_result = rollback_install(modpack_dir=modpack, backup_dir=install_result.backup_dir)

    assert rollback_result.restored_files > 0
    assert rollback_result.removed_files > 0
    assert original_chapter.read_text(encoding="utf-8") == original_text
    assert not (modpack / "resourcepacks" / "mc-han-cn" / "pack.mcmeta").exists()


def write_scan_csv(tmp_path, modpack):
    csv_path = tmp_path / "scan.csv"
    write_extracted_csv(scan_modpack(modpack), csv_path)
    return csv_path
