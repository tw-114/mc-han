from __future__ import annotations

from collections import Counter
import csv
import json
import zipfile

import mc_han.scanner as scanner_module
from mc_han.cli import build_parser, main
from mc_han.extractors.jar import JarScanResult, scan_jar, scan_mod_jars
from mc_han.scanner import build_scan_report, scan_modpack, write_extracted_csv
from mc_han.utils.safe_zip import (
    ACTUAL_READ_LIMIT,
    COMPRESSION_RATIO_LIMIT,
    ENTRY_COUNT_LIMIT,
    ENTRY_SIZE_LIMIT,
    JAR_TOTAL_SIZE_LIMIT,
    ZipDiagnostic,
    SafeZipReader,
    ZipSafetyLimits,
)


def create_sample_modpack(tmp_path):
    modpack = tmp_path / "pack"
    mods = modpack / "mods"
    kubejs_lang = modpack / "kubejs" / "assets" / "demo" / "lang"
    resourcepack_lang = modpack / "resourcepacks" / "example_pack" / "assets" / "demo" / "lang"
    lang = modpack / "config" / "ftbquests" / "quests" / "lang"
    chapters = modpack / "config" / "ftbquests" / "quests" / "chapters"
    mods.mkdir(parents=True)
    kubejs_lang.mkdir(parents=True)
    resourcepack_lang.mkdir(parents=True)
    lang.mkdir(parents=True)
    chapters.mkdir(parents=True)

    (lang / "en_us.json").write_text(
        json.dumps(
            {
                "chapter.intro": "Welcome to the quest book.",
                "item.ae2.spatial_pylon": "Spatial Pylon",
                "block.ae2.controller": "ME Controller",
            }
        ),
        encoding="utf-8",
    )

    (kubejs_lang / "en_us.json").write_text(
        json.dumps(
            {
                "tooltip.demo.machine": "Generates power during storms.",
                "item.demo.machine": "Storm Machine",
            }
        ),
        encoding="utf-8",
    )
    (resourcepack_lang / "en_us.json").write_text(
        json.dumps(
            {
                "screen.demo.title": "Storm Control Panel",
                "block.demo.machine": "Storm Machine",
            }
        ),
        encoding="utf-8",
    )

    (chapters / "chapter_1.snbt").write_text(
        "\n".join(
            [
                "{",
                '  filename: "chapter_1"',
                '  title: "&e已汉化章节"',
                "  quests: [{",
                '    id: "07D72CC3A7E25F72"',
                '    title: "First Night"',
                '    subtitle: "Stay alive until dawn."',
                "    description: [",
                '      "Survive the first night before exploring caves."',
                '      "{image:demo:textures/quests/night.png width:170 height:125 align:center}"',
                '      "{@pagebreak}"',
                '      ""',
                '      "Use &aTorches&r to keep monsters away."',
                '      "按住 &6[Shift]&r 查看更多信息。"',
                '      "这里仍有 a long untranslated English sentence needing work."',
                "    ]",
                "  }]",
                "}",
            ]
        ),
        encoding="utf-8",
    )

    with zipfile.ZipFile(mods / "sample.jar", "w") as jar:
        jar.writestr(
            "assets/demo/lang/en_us.json",
            json.dumps(
                {
                    "message.demo.warning": "The ritual is unstable.",
                    "entity.demo.beast": "Demo Beast",
                }
            ),
        )
        jar.writestr(
            "assets/ae2/ae2guide/index.md",
            "\n".join(
                [
                    "# Getting Started",
                    "",
                    "Use the ME Terminal to store items.",
                    "",
                    "```json",
                    '{"do_not_translate": "This is code"}',
                    "```",
                    "",
                    '<ItemLink id="ae2:controller">Controller</ItemLink> stores channels.',
                ]
            ),
        )
        jar.writestr(
            "assets/demo/guides/page.md",
            "## Machine Guide\n\nCraft the first machine before automation.",
        )
        jar.writestr(
            "assets/demo/patchouli_books/book/en_us/entries/intro.json",
            json.dumps(
                {
                    "name": "Getting Started",
                    "icon": "ae2:certus_quartz_crystal",
                    "pages": [
                        {
                            "type": "patchouli:text",
                            "text": "Use $(item)Quartz$(0) to begin.",
                            "item": "ae2:certus_quartz_crystal",
                        }
                    ],
                }
            ),
        )
        jar.writestr(
            "assets/demo/modonomicon/books/book/en_us/entries/ritual.json",
            json.dumps(
                {
                    "title": "First Ritual",
                    "category": "demo:rituals",
                    "pages": [{"text": "Place chalk around the golden bowl."}],
                }
            ),
        )
    return modpack


def test_scan_modpack_extracts_supported_sources(tmp_path):
    modpack = create_sample_modpack(tmp_path)

    records = scan_modpack(modpack)
    originals = [record.original for record in records]
    source_types = {record.source_type for record in records}

    assert "ftbquests_lang" in source_types
    assert "jar_ae2guide" in source_types
    assert "jar_guides" in source_types
    assert "jar_patchouli" in source_types
    assert "jar_modonomicon" in source_types
    assert "jar_lang" in source_types
    assert "kubejs_lang" in source_types
    assert "resourcepack_lang" in source_types
    assert "ftbquests_snbt" in source_types
    assert "Welcome to the quest book." in originals
    assert "Generates power during storms." in originals
    assert "Storm Control Panel" in originals
    assert "The ritual is unstable." in originals
    assert "Storm Machine" not in originals
    assert "Demo Beast" not in originals
    assert "&e已汉化章节" not in originals
    assert "First Night" in originals
    assert "Stay alive until dawn." in originals
    assert "Survive the first night before exploring caves." in originals
    assert "Use &aTorches&r to keep monsters away." in originals
    assert "按住 &6[Shift]&r 查看更多信息。" not in originals
    assert "这里仍有 a long untranslated English sentence needing work." in originals
    assert "Spatial Pylon" not in originals
    assert "ME Controller" not in originals
    assert "{@pagebreak}" not in originals
    assert all("{image:" not in original for original in originals)
    assert all("This is code" not in original for original in originals)
    assert any("$(item)Quartz$(0)" in original for original in originals)
    assert any("golden bowl" in original for original in originals)


def test_write_extracted_csv_has_required_fields(tmp_path):
    modpack = create_sample_modpack(tmp_path)
    records = scan_modpack(modpack)
    output = tmp_path / "extracted_texts.csv"

    write_extracted_csv(records, output)

    with output.open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))

    assert rows
    assert set(rows[0]) == {
        "id",
        "source_type",
        "container",
        "file_path",
        "key_path",
        "original",
        "translation",
        "note",
        "review_status",
        "skip_status",
    }
    assert all(row["translation"] == "" for row in rows)


def test_cli_scan_writes_default_csv(tmp_path):
    modpack = create_sample_modpack(tmp_path)

    exit_code = main(["scan", str(modpack)])

    assert exit_code == 0
    assert (modpack / "extracted_texts.csv").exists()
    report = modpack / "scan_report.txt"
    assert report.exists()
    assert "ftbquests_snbt" in report.read_text(encoding="utf-8")


def test_build_scan_report_includes_inventory(tmp_path):
    modpack = create_sample_modpack(tmp_path)
    records = scan_modpack(modpack)

    report = build_scan_report(
        modpack_dir=modpack,
        records=records,
        output_csv=modpack / "extracted_texts.csv",
        elapsed_seconds=1.25,
    )

    assert "jar_files_found: 1" in report
    assert "ftb_lang_files_found: 1" in report
    assert "ftb_snbt_files_found: 1" in report
    assert "jar_patchouli" in report


def test_cli_parser_accepts_gui_command():
    args = build_parser().parse_args(["gui"])

    assert args.command == "gui"


def test_scan_jar_skips_unsafe_entries_and_keeps_normal_entries(tmp_path):
    jar_path = tmp_path / "paths.jar"
    with zipfile.ZipFile(jar_path, "w") as jar:
        jar.writestr("assets/../../../escape/ae2guide/page.md", "Unsafe guide text.")
        jar.writestr("assets/../../escape/lang/en_us.json", json.dumps({"message.bad": "Unsafe text."}))
        jar.writestr(r"assets\demo\..\escape\guides\page.md", "Unsafe Windows path.")
        jar.writestr("assets/demo/ae2guide/page.md", "Safe guide text.")
        jar.writestr("assets/demo/lang/en_us.json", json.dumps({"message.safe": "Safe language text."}))

    records = scan_jar(jar_path, container="mods/paths.jar")

    assert {record.file_path for record in records} == {
        "assets/demo/ae2guide/page.md",
        "assets/demo/lang/en_us.json",
    }
    assert {record.original for record in records} == {
        "Safe guide text.",
        "Safe language text.",
    }


def test_scan_jar_skips_oversized_entry_and_keeps_safe_prior_result(tmp_path):
    jar_path = tmp_path / "limits.jar"
    with zipfile.ZipFile(jar_path, "w", compression=zipfile.ZIP_DEFLATED) as jar:
        jar.writestr("assets/demo/ae2guide/a-safe.md", "Safe guide text.")
        jar.writestr("assets/demo/ae2guide/z-large.md", "x" * 200)
    diagnostics = []
    limits = ZipSafetyLimits(
        max_entries=20,
        max_entry_uncompressed=100,
        max_candidate_uncompressed_total=500,
        max_actual_read_total=500,
        max_compression_ratio=500.0,
        chunk_size=16,
    )

    records = scan_jar(
        jar_path,
        container="mods/limits.jar",
        limits=limits,
        diagnostics=diagnostics,
    )

    assert [record.original for record in records] == ["Safe guide text."]
    assert all(record.file_path != "assets/demo/ae2guide/z-large.md" for record in records)
    assert [item.code for _container, item in diagnostics] == [ENTRY_SIZE_LIMIT]


def test_jar_total_stop_preserves_safe_records_and_next_jar_is_scanned(tmp_path):
    modpack = tmp_path / "pack"
    mods_dir = modpack / "mods"
    mods_dir.mkdir(parents=True)
    with zipfile.ZipFile(mods_dir / "a-limited.jar", "w") as jar:
        jar.writestr("assets/demo/ae2guide/a-safe.md", "Safe first.")
        jar.writestr("assets/demo/ae2guide/b-limit.md", "Second entry is too much.")
        jar.writestr("assets/demo/ae2guide/c-unread.md", "Must not be opened.")
    with zipfile.ZipFile(mods_dir / "z-normal.jar", "w") as jar:
        jar.writestr("assets/demo/ae2guide/page.md", "Later JAR remains safe.")
    limits = ZipSafetyLimits(
        max_entries=20,
        max_entry_uncompressed=100,
        max_candidate_uncompressed_total=30,
        max_actual_read_total=200,
        max_compression_ratio=200.0,
        chunk_size=8,
    )

    records = scan_mod_jars(modpack, limits=limits)

    assert {record.original for record in records} == {
        "Safe first.",
        "Later JAR remains safe.",
    }


def test_scan_jar_records_bad_zip_without_crashing(tmp_path):
    jar_path = tmp_path / "broken.jar"
    jar_path.write_bytes(b"not a zip archive")
    diagnostics = []

    records = scan_jar(
        jar_path,
        container="mods/broken.jar",
        diagnostics=diagnostics,
    )

    assert records == []
    assert diagnostics[0][1].code == "bad_zip"


def test_scan_report_summarizes_zip_limits_with_relative_paths(tmp_path, monkeypatch):
    modpack = tmp_path / "pack"
    mods_dir = modpack / "mods"
    mods_dir.mkdir(parents=True)
    names_and_diagnostics = {
        "entry-count.jar": ZipDiagnostic(
            ENTRY_COUNT_LIMIT,
            None,
            "too many entries",
            stops_jar=True,
        ),
        "entry-size.jar": ZipDiagnostic(
            ENTRY_SIZE_LIMIT,
            "assets/demo/ae2guide/large.md",
            "entry too large",
        ),
        "total-size.jar": ZipDiagnostic(
            JAR_TOTAL_SIZE_LIMIT,
            "assets/demo/guides/page.md",
            "candidate total too large",
            stops_jar=True,
        ),
        "ratio.jar": ZipDiagnostic(
            COMPRESSION_RATIO_LIMIT,
            "assets/demo/lang/en_us.json",
            "compression ratio too high",
        ),
        "actual-read.jar": ZipDiagnostic(
            ACTUAL_READ_LIMIT,
            "assets/demo/ae2guide/page.md",
            "actual read too large",
        ),
    }
    for jar_name in names_and_diagnostics:
        (mods_dir / jar_name).write_bytes(b"placeholder")

    def fake_inspection(
        jar_path,
        *,
        container,
        translate_names=False,
        limits,
        read_contents=True,
    ):
        return JarScanResult(
            records=[],
            supported_entries=Counter(),
            diagnostics=[names_and_diagnostics[jar_path.name]],
        )

    monkeypatch.setattr(scanner_module, "inspect_and_scan_jar", fake_inspection)

    report = build_scan_report(
        modpack_dir=modpack,
        records=[],
        output_csv=modpack / "extracted_texts.csv",
    )

    assert "jars_stopped_by_entry_count_limit: 1" in report
    assert "entries_rejected_by_size_limit: 1" in report
    assert "jars_stopped_by_candidate_total_limit: 1" in report
    assert "entries_rejected_by_compression_ratio: 1" in report
    assert "entries_rejected_by_actual_read_limit: 1" in report
    assert "[entry_size_limit] mods/entry-size.jar :: assets/demo/ae2guide/large.md" in report
    assert str(tmp_path) not in report


def test_scan_and_report_reuse_same_jar_read_and_diagnostics(tmp_path, monkeypatch):
    modpack = tmp_path / "pack"
    mods_dir = modpack / "mods"
    mods_dir.mkdir(parents=True)
    with zipfile.ZipFile(mods_dir / "limits.jar", "w") as jar:
        jar.writestr("assets/demo/ae2guide/a-safe.md", "Safe guide text.")
        jar.writestr("assets/demo/ae2guide/b-large.md", "x" * 200)
    limits = ZipSafetyLimits(
        max_entries=20,
        max_entry_uncompressed=100,
        max_candidate_uncompressed_total=500,
        max_actual_read_total=500,
        max_compression_ratio=500.0,
        chunk_size=16,
    )
    original_read_entry = SafeZipReader.read_entry
    read_calls = []

    def count_reads(self, info):
        read_calls.append(info.filename)
        return original_read_entry(self, info)

    monkeypatch.setattr(SafeZipReader, "read_entry", count_reads)

    records = scan_modpack(modpack, zip_limits=limits)
    reads_after_scan = list(read_calls)
    report = build_scan_report(
        modpack_dir=modpack,
        records=records,
        output_csv=modpack / "extracted_texts.csv",
        zip_limits=limits,
    )

    assert len(records) == 1
    assert reads_after_scan == ["assets/demo/ae2guide/a-safe.md"]
    assert read_calls == reads_after_scan
    assert "entries_rejected_by_size_limit: 1" in report
    assert "[entry_size_limit] mods/limits.jar :: assets/demo/ae2guide/b-large.md" in report
