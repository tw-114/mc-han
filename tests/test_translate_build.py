from __future__ import annotations

import json
import os

import pytest

import mc_han.builder.installer as installer_module
import mc_han.builder.resourcepack as resourcepack_module
from mc_han.builder.resourcepack import (
    build_client_resourcepack,
    build_complete_install_package,
    build_outputs,
    build_server_pack,
    to_config_overlay_path,
    to_resourcepack_path,
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


def test_build_rejects_tampered_csv_jar_output_path(tmp_path):
    modpack = tmp_path / "pack"
    modpack.mkdir()
    csv_path = tmp_path / "translated.csv"
    output_dir = tmp_path / "build"
    write_extracted_csv(
        [
            ExtractedText(
                id="unsafe",
                source_type="jar_lang",
                container="mods/unsafe.jar",
                file_path="assets/../../../escape/lang/en_us.json",
                key_path="message.unsafe",
                original="Unsafe",
                translation="不安全",
            )
        ],
        csv_path,
    )

    with pytest.raises(ValueError, match="dot path segment"):
        build_outputs(modpack_dir=modpack, csv_path=csv_path, output_dir=output_dir)

    assert not (tmp_path / "escape" / "lang" / "zh_cn.json").exists()


@pytest.mark.parametrize(
    "container",
    [
        "../outside.jar",
        r"C:\outside.jar",
        "C:/outside.jar",
        r"\\server\share\outside.jar",
        "other/demo.jar",
        "modpack",
    ],
)
def test_build_prevalidates_all_csv_jar_containers_before_output(tmp_path, container):
    modpack = tmp_path / "pack"
    (modpack / "other").mkdir(parents=True)
    (modpack / "other" / "demo.jar").write_bytes(b"not a jar")
    csv_path = tmp_path / "translated.csv"
    output_dir = tmp_path / "build"
    write_extracted_csv(
        [
            ExtractedText(
                id="container",
                source_type="jar_lang",
                container=container,
                file_path="assets/demo/lang/en_us.json",
                key_path="message.demo",
                original="Demo",
                translation="演示",
            )
        ],
        csv_path,
    )

    with pytest.raises(ValueError):
        build_outputs(modpack_dir=modpack, csv_path=csv_path, output_dir=output_dir)

    assert not output_dir.exists()


def test_build_accepts_valid_jar_container_under_mods(tmp_path):
    modpack = tmp_path / "pack"
    jar_path = modpack / "mods" / "demo.jar"
    jar_path.parent.mkdir(parents=True)
    jar_path.write_bytes(b"jar container is not read for jar_lang")
    csv_path = tmp_path / "translated.csv"
    output_dir = tmp_path / "build"
    write_extracted_csv(
        [
            ExtractedText(
                id="container",
                source_type="jar_lang",
                container="mods/demo.jar",
                file_path="assets/demo/lang/en_us.json",
                key_path="message.demo",
                original="Demo",
                translation="演示",
            )
        ],
        csv_path,
    )

    build_outputs(modpack_dir=modpack, csv_path=csv_path, output_dir=output_dir)

    assert (
        output_dir
        / "resourcepacks"
        / "mc-han-cn"
        / "assets"
        / "demo"
        / "lang"
        / "zh_cn.json"
    ).exists()


def test_build_rejects_jar_container_symlink_outside_modpack(tmp_path):
    modpack = tmp_path / "pack"
    mods_dir = modpack / "mods"
    mods_dir.mkdir(parents=True)
    outside_jar = tmp_path / "outside.jar"
    outside_jar.write_bytes(b"outside")
    linked_jar = mods_dir / "linked.jar"
    try:
        linked_jar.symlink_to(outside_jar)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"File symlinks are unavailable: {error}")
    csv_path = tmp_path / "translated.csv"
    output_dir = tmp_path / "build"
    write_extracted_csv(
        [
            ExtractedText(
                id="container-link",
                source_type="jar_lang",
                container="mods/linked.jar",
                file_path="assets/demo/lang/en_us.json",
                key_path="message.demo",
                original="Demo",
                translation="演示",
            )
        ],
        csv_path,
    )

    with pytest.raises(ValueError):
        build_outputs(modpack_dir=modpack, csv_path=csv_path, output_dir=output_dir)

    assert not output_dir.exists()


def test_language_path_rewrites_keep_original_scope():
    assert (
        to_config_overlay_path("config/ftbquests/quests/lang/en_us.json").as_posix()
        == "config/ftbquests/quests/lang/zh_cn.json"
    )
    assert (
        to_config_overlay_path("config/ftbquests/quests/lang/en_us.snbt").as_posix()
        == "config/ftbquests/quests/lang/zh_cn.snbt"
    )
    assert (
        to_config_overlay_path("config/ftbquests/quests/lang/en_us/chapter.snbt").as_posix()
        == "config/ftbquests/quests/lang/zh_cn/chapter.snbt"
    )
    assert (
        to_config_overlay_path("config/unrelated/en_us/file.txt").as_posix()
        == "config/unrelated/en_us/file.txt"
    )
    assert (
        to_resourcepack_path(
            "assets/demo/patchouli_books/book/en_us/entries/intro.json",
            source_type="jar_patchouli",
        ).as_posix()
        == "assets/demo/patchouli_books/book/zh_cn/entries/intro.json"
    )
    assert (
        to_resourcepack_path(
            "assets/demo/modonomicon/books/book/en_us/entries/intro.json",
            source_type="jar_modonomicon",
        ).as_posix()
        == "assets/demo/modonomicon/books/book/zh_cn/entries/intro.json"
    )
    assert (
        to_resourcepack_path(
            "assets/demo/unrelated/en_us/file.json",
            source_type="jar_guides",
        ).as_posix()
        == "assets/demo/unrelated/en_us/file.json"
    )


def test_rollback_rejects_target_escape_without_deleting_outside_file(tmp_path):
    modpack = tmp_path / "pack"
    backup_dir = tmp_path / "backup"
    modpack.mkdir()
    backup_dir.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    write_manifest(
        backup_dir,
        [{"relative_target": "../outside.txt", "had_backup": False, "backup_relative": ""}],
    )

    with pytest.raises(RuntimeError, match="Unsafe rollback manifest"):
        rollback_install(modpack_dir=modpack, backup_dir=backup_dir)

    assert outside.read_text(encoding="utf-8") == "keep"


def test_rollback_rejects_unexpected_top_level_directory(tmp_path):
    modpack = tmp_path / "pack"
    backup_dir = tmp_path / "backup"
    target = modpack / "mods" / "do-not-touch.jar"
    target.parent.mkdir(parents=True)
    target.write_text("keep", encoding="utf-8")
    backup_dir.mkdir()
    write_manifest(
        backup_dir,
        [{"relative_target": "mods/do-not-touch.jar", "had_backup": False, "backup_relative": ""}],
    )

    with pytest.raises(RuntimeError, match="must start with one of"):
        rollback_install(modpack_dir=modpack, backup_dir=backup_dir)

    assert target.read_text(encoding="utf-8") == "keep"


def test_rollback_rejects_backup_escape_without_reading_outside_backup(tmp_path):
    modpack = tmp_path / "pack"
    backup_dir = tmp_path / "backup"
    target = modpack / "config" / "demo.txt"
    target.parent.mkdir(parents=True)
    target.write_text("installed", encoding="utf-8")
    backup_dir.mkdir()
    outside_backup = tmp_path / "outside-backup.txt"
    outside_backup.write_text("outside", encoding="utf-8")
    write_manifest(
        backup_dir,
        [
            {
                "relative_target": "config/demo.txt",
                "had_backup": True,
                "backup_relative": "../outside-backup.txt",
            }
        ],
    )

    with pytest.raises(RuntimeError, match="Unsafe rollback manifest"):
        rollback_install(modpack_dir=modpack, backup_dir=backup_dir)

    assert target.read_text(encoding="utf-8") == "installed"
    assert outside_backup.read_text(encoding="utf-8") == "outside"


def test_rollback_validates_entire_manifest_before_modifying_any_file(tmp_path):
    modpack = tmp_path / "pack"
    backup_dir = tmp_path / "backup"
    legal_target = modpack / "config" / "legal.txt"
    legal_target.parent.mkdir(parents=True)
    legal_target.write_text("installed", encoding="utf-8")
    backup_dir.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    write_manifest(
        backup_dir,
        [
            {"relative_target": "config/legal.txt", "had_backup": False, "backup_relative": ""},
            {"relative_target": "../outside.txt", "had_backup": False, "backup_relative": ""},
        ],
    )

    with pytest.raises(RuntimeError, match="Unsafe rollback manifest"):
        rollback_install(modpack_dir=modpack, backup_dir=backup_dir)

    assert legal_target.read_text(encoding="utf-8") == "installed"
    assert outside.read_text(encoding="utf-8") == "keep"


def test_rollback_accepts_legal_legacy_manifest_without_backup_relative(tmp_path):
    modpack = tmp_path / "pack"
    backup_dir = tmp_path / "backup"
    target = modpack / "config" / "demo.txt"
    backup = backup_dir / "config" / "demo.txt"
    target.parent.mkdir(parents=True)
    backup.parent.mkdir(parents=True)
    target.write_text("installed", encoding="utf-8")
    backup.write_text("original", encoding="utf-8")
    write_manifest(
        backup_dir,
        [{"relative_target": "config/demo.txt", "had_backup": True}],
    )

    result = rollback_install(modpack_dir=modpack, backup_dir=backup_dir)

    assert result.restored_files == 1
    assert target.read_text(encoding="utf-8") == "original"


def test_rollback_rejects_existing_symlink_target_escape(tmp_path):
    modpack = tmp_path / "pack"
    backup_dir = tmp_path / "backup"
    config_dir = modpack / "config"
    config_dir.mkdir(parents=True)
    backup_dir.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    link = config_dir / "linked.txt"
    try:
        link.symlink_to(outside)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"File symlinks are unavailable: {error}")
    write_manifest(
        backup_dir,
        [{"relative_target": "config/linked.txt", "had_backup": False, "backup_relative": ""}],
    )

    with pytest.raises(RuntimeError, match="Unsafe rollback manifest"):
        rollback_install(modpack_dir=modpack, backup_dir=backup_dir)

    assert outside.read_text(encoding="utf-8") == "keep"
    assert link.is_symlink()


def test_rollback_rejects_duplicate_relative_target(tmp_path):
    modpack = tmp_path / "pack"
    backup_dir = tmp_path / "backup"
    modpack.mkdir()
    backup_dir.mkdir()
    write_manifest(
        backup_dir,
        [
            {"relative_target": "config/demo.txt", "had_backup": False},
            {"relative_target": "config/demo.txt", "had_backup": False},
        ],
    )

    with pytest.raises(RuntimeError, match="duplicate target"):
        rollback_install(modpack_dir=modpack, backup_dir=backup_dir)


@pytest.mark.skipif(os.name != "nt", reason="Case-insensitive identity is Windows-specific")
def test_rollback_rejects_different_case_paths_to_same_windows_target(tmp_path):
    modpack = tmp_path / "pack"
    backup_dir = tmp_path / "backup"
    modpack.mkdir()
    backup_dir.mkdir()
    write_manifest(
        backup_dir,
        [
            {"relative_target": "config/Demo.txt", "had_backup": False},
            {"relative_target": "CONFIG/demo.TXT", "had_backup": False},
        ],
    )

    with pytest.raises(RuntimeError, match="duplicate target"):
        rollback_install(modpack_dir=modpack, backup_dir=backup_dir)


def test_rollback_rejects_duplicate_target_with_conflicting_backup_state(tmp_path):
    modpack = tmp_path / "pack"
    backup_dir = tmp_path / "backup"
    modpack.mkdir()
    backup_dir.mkdir()
    write_manifest(
        backup_dir,
        [
            {"relative_target": "config/demo.txt", "had_backup": True},
            {"relative_target": "config/demo.txt", "had_backup": False},
        ],
    )

    with pytest.raises(RuntimeError, match="duplicate target"):
        rollback_install(modpack_dir=modpack, backup_dir=backup_dir)


def test_rollback_duplicate_after_legal_item_keeps_all_targets_unchanged(tmp_path):
    modpack = tmp_path / "pack"
    backup_dir = tmp_path / "backup"
    legal_target = modpack / "config" / "legal.txt"
    duplicate_target = modpack / "config" / "duplicate.txt"
    legal_target.parent.mkdir(parents=True)
    legal_target.write_text("legal", encoding="utf-8")
    duplicate_target.write_text("duplicate", encoding="utf-8")
    backup_dir.mkdir()
    write_manifest(
        backup_dir,
        [
            {"relative_target": "config/legal.txt", "had_backup": False},
            {"relative_target": "config/duplicate.txt", "had_backup": False},
            {"relative_target": "config/duplicate.txt", "had_backup": False},
        ],
    )

    with pytest.raises(RuntimeError, match="duplicate target"):
        rollback_install(modpack_dir=modpack, backup_dir=backup_dir)

    assert legal_target.read_text(encoding="utf-8") == "legal"
    assert duplicate_target.read_text(encoding="utf-8") == "duplicate"


def test_rollback_revalidates_before_delete_after_parent_link_swap(tmp_path, monkeypatch):
    modpack = tmp_path / "pack"
    backup_dir = tmp_path / "backup"
    config_dir = modpack / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "demo.txt").write_text("installed", encoding="utf-8")
    backup_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_file = outside / "demo.txt"
    outside_file.write_text("outside", encoding="utf-8")
    write_manifest(
        backup_dir,
        [{"relative_target": "config/demo.txt", "had_backup": False}],
    )
    original_validate = installer_module.validate_rollback_manifest

    def validate_then_swap(*args, **kwargs):
        plan = original_validate(*args, **kwargs)
        replace_directory_with_link(config_dir, outside, tmp_path / "held-delete")
        return plan

    monkeypatch.setattr(installer_module, "validate_rollback_manifest", validate_then_swap)

    with pytest.raises(RuntimeError, match="Unsafe rollback path changed"):
        rollback_install(modpack_dir=modpack, backup_dir=backup_dir)

    assert outside_file.read_text(encoding="utf-8") == "outside"


def test_rollback_revalidates_before_restore_after_parent_link_swap(tmp_path, monkeypatch):
    modpack = tmp_path / "pack"
    backup_dir = tmp_path / "backup"
    config_dir = modpack / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "demo.txt").write_text("installed", encoding="utf-8")
    backup = backup_dir / "config" / "demo.txt"
    backup.parent.mkdir(parents=True)
    backup.write_text("original", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_file = outside / "demo.txt"
    outside_file.write_text("outside", encoding="utf-8")
    write_manifest(
        backup_dir,
        [{"relative_target": "config/demo.txt", "had_backup": True}],
    )
    original_validate = installer_module.validate_rollback_manifest

    def validate_then_swap(*args, **kwargs):
        plan = original_validate(*args, **kwargs)
        replace_directory_with_link(config_dir, outside, tmp_path / "held-restore")
        return plan

    monkeypatch.setattr(installer_module, "validate_rollback_manifest", validate_then_swap)

    with pytest.raises(RuntimeError, match="Unsafe rollback path changed"):
        rollback_install(modpack_dir=modpack, backup_dir=backup_dir)

    assert outside_file.read_text(encoding="utf-8") == "outside"


def test_build_revalidates_output_after_parent_link_swap(tmp_path, monkeypatch):
    modpack = tmp_path / "pack"
    jar_path = modpack / "mods" / "demo.jar"
    jar_path.parent.mkdir(parents=True)
    jar_path.write_bytes(b"unused")
    csv_path = tmp_path / "translated.csv"
    output_dir = tmp_path / "build"
    write_extracted_csv(
        [
            ExtractedText(
                id="write-race",
                source_type="jar_lang",
                container="mods/demo.jar",
                file_path="assets/demo/lang/en_us.json",
                key_path="message.demo",
                original="Demo",
                translation="演示",
            )
        ],
        csv_path,
    )
    pack_root = output_dir / "resourcepacks" / "mc-han-cn"
    outside = tmp_path / "outside"
    outside.mkdir()
    original_write_pack_mcmeta = resourcepack_module.write_pack_mcmeta

    def write_metadata_then_swap(root):
        original_write_pack_mcmeta(root)
        assets = pack_root / "assets"
        assets.mkdir()
        replace_directory_with_link(assets, outside, tmp_path / "held-assets")

    monkeypatch.setattr(resourcepack_module, "write_pack_mcmeta", write_metadata_then_swap)

    with pytest.raises(ValueError, match="symbolic link|reparse point"):
        build_outputs(modpack_dir=modpack, csv_path=csv_path, output_dir=output_dir)

    assert not (outside / "demo" / "lang" / "zh_cn.json").exists()


def write_scan_csv(tmp_path, modpack):
    csv_path = tmp_path / "scan.csv"
    write_extracted_csv(scan_modpack(modpack), csv_path)
    return csv_path


def write_manifest(backup_dir, items):
    (backup_dir / "install_manifest.json").write_text(
        json.dumps({"version": 1, "items": items}),
        encoding="utf-8",
    )


def replace_directory_with_link(path, target, held_path):
    path.rename(held_path)
    try:
        path.symlink_to(target, target_is_directory=True)
    except (NotImplementedError, OSError) as error:
        held_path.rename(path)
        pytest.skip(f"Directory symlinks are unavailable: {error}")
