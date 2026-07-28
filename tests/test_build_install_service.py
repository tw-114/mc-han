from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from mc_han.builder.resourcepack import resource_pack_format_for_version
from mc_han.csv_store import write_extracted_csv
from mc_han.models import ExtractedText
from mc_han.services.build_install import (
    EXPORT_ARCHIVE_NAME,
    build_localization_package,
    export_localization_zip,
    install_localization_package,
    rollback_localization_install,
)


def _project(tmp_path: Path) -> tuple[Path, Path, Path]:
    modpack = tmp_path / "pack"
    jar_path = modpack / "mods" / "demo.jar"
    jar_path.parent.mkdir(parents=True)
    jar_path.write_bytes(b"read-only jar input")
    csv_path = modpack / ".mc-han" / "extracted_texts.csv"
    write_extracted_csv(
        [
            ExtractedText(
                id="demo",
                source_type="jar_lang",
                container="mods/demo.jar",
                file_path="assets/demo/lang/en_us.json",
                key_path="demo.description",
                original="Demo description",
                translation="演示说明",
            )
        ],
        csv_path,
    )
    return modpack, csv_path, modpack / ".mc-han" / "output"


@pytest.mark.parametrize(
    ("version", "expected"),
    (
        ("1.12.2", 3),
        ("1.20.1", 15),
        ("1.20.4", 22),
        ("1.20.6", 32),
        ("1.21.1", 34),
        ("1.21.4", 46),
        ("1.21.8", 64),
    ),
)
def test_resource_pack_format_uses_minecraft_version(version, expected):
    assert resource_pack_format_for_version(version) == (expected, True)


def test_build_export_install_and_manifest_rollback(tmp_path: Path):
    modpack, csv_path, output_dir = _project(tmp_path)
    original_jar = (modpack / "mods" / "demo.jar").read_bytes()
    original_mcmeta = modpack / "resourcepacks" / "mc-han-cn" / "pack.mcmeta"
    original_mcmeta.parent.mkdir(parents=True)
    original_mcmeta.write_text("original", encoding="utf-8")

    build = build_localization_package(
        modpack_dir=modpack,
        csv_path=csv_path,
        output_dir=output_dir,
        minecraft_version="1.20.4",
    )

    generated_mcmeta = (
        output_dir
        / "resourcepacks"
        / "mc-han-cn"
        / "pack.mcmeta"
    )
    assert json.loads(generated_mcmeta.read_text(encoding="utf-8"))[
        "pack"
    ]["pack_format"] == 22
    assert build.pack_format == 22
    assert build.resource_files == 1
    assert build.config_files == 0
    assert build.installable_files >= 2
    assert build.output_file_name == EXPORT_ARCHIVE_NAME
    assert build.errors == ()

    exported = export_localization_zip(output_dir=output_dir)
    assert exported.archive_path.name == EXPORT_ARCHIVE_NAME
    assert exported.archive_size > 0
    with zipfile.ZipFile(exported.archive_path) as archive:
        assert (
            "resourcepacks/mc-han-cn/pack.mcmeta"
            in archive.namelist()
        )

    installed = install_localization_package(
        modpack_dir=modpack,
        output_dir=output_dir,
    )
    assert installed.installed_files == build.installable_files
    assert installed.backed_up_files >= 1
    assert installed.manifest_path is not None
    assert installed.manifest_path.exists()
    assert "pack_format" in original_mcmeta.read_text(encoding="utf-8")

    rolled_back = rollback_localization_install(
        modpack_dir=modpack,
        backup_dir=installed.backup_dir,
    )
    assert rolled_back.restored_files >= 1
    assert original_mcmeta.read_text(encoding="utf-8") == "original"
    assert (modpack / "mods" / "demo.jar").read_bytes() == original_jar


def test_unknown_version_warns_and_uses_compatible_default(tmp_path: Path):
    modpack, csv_path, output_dir = _project(tmp_path)

    result = build_localization_package(
        modpack_dir=modpack,
        csv_path=csv_path,
        output_dir=output_dir,
        minecraft_version="unknown",
    )

    assert result.pack_format == 15
    assert any("兼容默认值 15" in warning for warning in result.warnings)


def test_build_failure_does_not_delete_existing_success_output(
    tmp_path: Path,
):
    modpack, csv_path, output_dir = _project(tmp_path)
    output_dir.mkdir(parents=True)
    sentinel = output_dir / "previous-success.txt"
    sentinel.write_text("keep", encoding="utf-8")
    write_extracted_csv(
        [
            ExtractedText(
                id="unsafe",
                source_type="jar_lang",
                container="../outside.jar",
                file_path="assets/demo/lang/en_us.json",
                key_path="demo",
                original="Demo",
                translation="演示",
            )
        ],
        csv_path,
    )

    with pytest.raises(ValueError):
        build_localization_package(
            modpack_dir=modpack,
            csv_path=csv_path,
            output_dir=output_dir,
            minecraft_version="1.20.1",
        )

    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert not (tmp_path / "outside.jar").exists()
