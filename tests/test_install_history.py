from __future__ import annotations

import json
from pathlib import Path

from mc_han.builder.installer import InstallResult
from mc_han.core.project import project_paths
from mc_han.csv_store import write_extracted_csv
from mc_han.models import ExtractedText
from mc_han.services.build_install import (
    build_localization_package,
    install_localization_package,
    rollback_localization_install,
)
from mc_han.services.install_history import (
    InstallHistoryEntry,
    InstallHistoryStore,
    recover_latest_install_result,
)


def _built_project(tmp_path: Path) -> tuple[Path, Path]:
    modpack = tmp_path / "pack"
    jar_path = modpack / "mods" / "demo.jar"
    jar_path.parent.mkdir(parents=True)
    jar_path.write_bytes(b"read-only jar input")
    csv_path = project_paths(modpack).extracted_csv
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
    output_dir = project_paths(modpack).output_dir
    build_localization_package(
        modpack_dir=modpack,
        csv_path=csv_path,
        output_dir=output_dir,
        minecraft_version="1.20.4",
    )
    return modpack, output_dir


def test_install_history_survives_restart_and_rollback_is_precise(
    tmp_path: Path,
):
    modpack, output_dir = _built_project(tmp_path)
    jar_path = modpack / "mods" / "demo.jar"
    original_jar = jar_path.read_bytes()
    existing = modpack / "resourcepacks" / "mc-han-cn" / "pack.mcmeta"
    existing.parent.mkdir(parents=True)
    existing.write_text("before install", encoding="utf-8")

    installed = install_localization_package(
        modpack_dir=modpack,
        output_dir=output_dir,
    )

    history_path = project_paths(modpack).install_history_json
    history_text = history_path.read_text(encoding="utf-8")
    assert str(tmp_path) not in history_text
    entries = InstallHistoryStore(modpack).load()
    assert len(entries) == 1
    assert entries[0].overwritten_files
    assert entries[0].generated_files

    recovered = recover_latest_install_result(modpack)
    assert recovered is not None
    assert recovered.backup_dir == installed.backup_dir.resolve()
    assert recovered.installed_files == installed.installed_files

    unrelated = modpack / "resourcepacks" / "unrelated.txt"
    unrelated.write_text("keep me", encoding="utf-8")
    rolled_back = rollback_localization_install(
        modpack_dir=modpack,
        backup_dir=recovered.backup_dir,
    )

    assert rolled_back.restored_files >= 1
    assert existing.read_text(encoding="utf-8") == "before install"
    assert unrelated.read_text(encoding="utf-8") == "keep me"
    assert jar_path.read_bytes() == original_jar
    assert recover_latest_install_result(modpack) is None
    assert InstallHistoryStore(modpack).load()[0].result == "rolled_back"


def test_old_manifest_without_history_remains_recoverable(tmp_path: Path):
    modpack, output_dir = _built_project(tmp_path)
    installed = install_localization_package(
        modpack_dir=modpack,
        output_dir=output_dir,
    )
    project_paths(modpack).install_history_json.unlink()

    recovered = recover_latest_install_result(modpack)

    assert recovered is not None
    assert recovered.backup_dir == installed.backup_dir.resolve()


def test_unsafe_or_corrupt_history_is_not_offered_for_rollback(
    tmp_path: Path,
):
    modpack = tmp_path / "pack"
    history_path = project_paths(modpack).install_history_json
    history_path.parent.mkdir(parents=True)
    history_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "entries": [
                    {
                        "install_id": "unsafe",
                        "installed_at": "2026-07-29T10:00:00+00:00",
                        "generated_files": ["resourcepacks/demo.txt"],
                        "overwritten_files": [],
                        "backup_relative": "../outside",
                        "manifest_relative": "../outside/install_manifest.json",
                        "result": "installed",
                        "rolled_back_at": "",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert InstallHistoryStore(modpack).load() == ()
    assert recover_latest_install_result(modpack) is None


def test_history_write_failure_does_not_hide_successful_install(
    tmp_path: Path,
    monkeypatch,
):
    modpack, output_dir = _built_project(tmp_path)

    def fail_save(_self, _entries):
        raise OSError("simulated")

    monkeypatch.setattr(InstallHistoryStore, "save", fail_save)
    result = install_localization_package(
        modpack_dir=modpack,
        output_dir=output_dir,
    )

    assert isinstance(result, InstallResult)
    assert result.history_saved is False
    assert result.manifest_path is not None
    assert result.manifest_path.exists()
    assert recover_latest_install_result(modpack) is not None


def test_install_history_entry_rejects_overlapping_targets():
    try:
        InstallHistoryEntry(
            install_id="one",
            installed_at="2026-07-29T10:00:00+00:00",
            generated_files=("resourcepacks/demo.txt",),
            overwritten_files=("resourcepacks/demo.txt",),
            backup_relative=".mc-han/backups/one",
            manifest_relative=(
                ".mc-han/backups/one/install_manifest.json"
            ),
        )
    except ValueError as error:
        assert "overlap" in str(error)
    else:
        raise AssertionError("overlapping history targets were accepted")
