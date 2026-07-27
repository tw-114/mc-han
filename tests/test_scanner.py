from __future__ import annotations

import csv
import json
import zipfile

from mc_han.cli import build_parser, main
from mc_han.extractors.jar import scan_jar
from mc_han.scanner import build_scan_report, scan_modpack, write_extracted_csv


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
