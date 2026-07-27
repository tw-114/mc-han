from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import mc_han.services.modpack_inspector as inspector_module
from mc_han.cli import main
from mc_han.services.modpack_inspector import inspect_modpack, sanitize_metadata_text
from mc_han.utils.safe_zip import ZipSafetyLimits
from mc_han.workflow.models import (
    ChineseResourceStatus,
    ContentCapability,
    ExistingChineseResources,
    InspectionMessage,
    InspectionValidity,
    LoaderInfo,
    ModpackInspection,
)


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def write_jar(path: Path, entries: dict[str, str] | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in (entries or {"META-INF/MANIFEST.MF": "Manifest-Version: 1.0"}).items():
            archive.writestr(name, content)
    return path


def curseforge_manifest(
    *,
    name: str = "ATM 9",
    minecraft: str = "1.20.1",
    loader: str = "neoforge-47.1.0",
) -> dict[str, object]:
    return {
        "manifestType": "minecraftModpack",
        "name": name,
        "minecraft": {
            "version": minecraft,
            "modLoaders": [{"id": loader, "primary": True}],
        },
    }


def capability(inspection, key: str):
    return next(item for item in inspection.capabilities if item.key == key)


def test_inspect_curseforge_metadata_is_valid(tmp_path: Path):
    pack = tmp_path / "curseforge-pack"
    write_json(pack / "manifest.json", curseforge_manifest())

    result = inspect_modpack(pack)

    assert result.validity is InspectionValidity.VALID
    assert result.display_name == "ATM 9"
    assert result.minecraft_version == "1.20.1"
    assert result.loader_name == "NeoForge"
    assert result.loader_version == "47.1.0"
    assert result.can_continue


def test_inspect_prism_multimc_metadata(tmp_path: Path):
    pack = tmp_path / "prism-pack"
    write_json(
        pack / "mmc-pack.json",
        {
            "components": [
                {"uid": "net.minecraft", "version": "1.20.4"},
                {"uid": "net.fabricmc.fabric-loader", "version": "0.15.11"},
            ]
        },
    )
    (pack / "instance.cfg").write_text("Name=Prism Example\n", encoding="utf-8")

    result = inspect_modpack(pack)

    assert result.validity is InspectionValidity.VALID
    assert result.display_name == "Prism Example"
    assert result.minecraft_version == "1.20.4"
    assert result.loader == result.loader.__class__("Fabric", "0.15.11")


def test_inspect_without_metadata_but_with_mods_and_config_is_probable(tmp_path: Path):
    pack = tmp_path / "manual-pack"
    write_jar(pack / "mods" / "demo.jar")
    (pack / "config").mkdir()

    result = inspect_modpack(pack)

    assert result.validity is InspectionValidity.PROBABLE
    assert result.can_continue
    assert any(message.code == "incomplete_instance_evidence" for message in result.messages)


def test_inspect_empty_directory_is_invalid(tmp_path: Path):
    pack = tmp_path / "empty"
    pack.mkdir()

    result = inspect_modpack(pack)

    assert result.validity is InspectionValidity.INVALID
    assert not result.can_continue
    assert any(message.code == "no_instance_evidence" for message in result.messages)


def test_inspect_selected_mods_subdirectory_warns_about_level(tmp_path: Path):
    pack = tmp_path / "pack"
    write_jar(pack / "mods" / "demo.jar")
    (pack / "config").mkdir()

    result = inspect_modpack(pack / "mods")

    assert result.validity is InspectionValidity.PROBABLE
    assert any(message.code == "wrong_directory_level" for message in result.messages)


@pytest.mark.parametrize(
    ("loader_id", "expected_name", "expected_version"),
    [
        ("forge-47.2.0", "Forge", "47.2.0"),
        ("neoforge-20.4.200", "NeoForge", "20.4.200"),
        ("fabric-loader-0.15.11", "Fabric", "0.15.11"),
        ("quilt-loader-0.24.0", "Quilt", "0.24.0"),
    ],
)
def test_inspect_recognizes_supported_loaders(
    tmp_path: Path,
    loader_id: str,
    expected_name: str,
    expected_version: str,
):
    pack = tmp_path / expected_name
    write_json(pack / "manifest.json", curseforge_manifest(loader=loader_id))

    result = inspect_modpack(pack)

    assert result.loader_name == expected_name
    assert result.loader_version == expected_version


def test_inspect_unknown_loader_does_not_guess(tmp_path: Path):
    pack = tmp_path / "unknown-loader"
    write_json(pack / "manifest.json", curseforge_manifest(loader="mystery-loader-1.0"))

    result = inspect_modpack(pack)

    assert result.validity is InspectionValidity.VALID
    assert result.loader_name == "unknown"
    assert any(message.code == "unsupported_loader_metadata" for message in result.messages)


def test_inspect_conflicting_metadata_returns_unknown_and_keeps_evidence(tmp_path: Path):
    pack = tmp_path / "conflict"
    write_json(
        pack / "manifest.json",
        curseforge_manifest(minecraft="1.20.1", loader="forge-47.2.0"),
    )
    write_json(
        pack / "mmc-pack.json",
        {
            "components": [
                {"uid": "net.minecraft", "version": "1.19.2"},
                {"uid": "net.fabricmc.fabric-loader", "version": "0.14.25"},
            ]
        },
    )

    result = inspect_modpack(pack)

    assert result.validity is InspectionValidity.VALID
    assert result.minecraft_version == "unknown"
    assert result.loader_name == "unknown"
    assert any(message.code == "minecraft_version_conflict" for message in result.messages)
    assert any(message.code == "loader_conflict" for message in result.messages)
    assert any("manifest.json: minecraft=1.20.1" == item for item in result.evidence)
    assert any("mmc-pack.json: minecraft=1.19.2" == item for item in result.evidence)


def test_inspect_recognized_and_unknown_loader_evidence_conflicts(tmp_path: Path):
    pack = tmp_path / "loader-conflict"
    write_json(
        pack / "manifest.json",
        {
            "name": "Conflicting Loaders",
            "minecraft": {
                "version": "1.20.1",
                "modLoaders": [
                    {"id": "forge-47.2.0"},
                    {"id": "mystery-loader-1.0"},
                ],
            },
        },
    )

    result = inspect_modpack(pack)

    assert result.loader_name == "unknown"
    assert any(message.code == "loader_conflict" for message in result.messages)


def test_inspect_counts_mod_jars_only(tmp_path: Path):
    pack = tmp_path / "count-pack"
    write_jar(pack / "mods" / "first.jar")
    write_jar(pack / "mods" / "second.JAR")
    (pack / "mods" / "notes.txt").write_text("not a mod", encoding="utf-8")

    result = inspect_modpack(pack)

    assert result.mod_count == 2


def test_inspect_detects_supported_content_capabilities(tmp_path: Path):
    pack = tmp_path / "capabilities"
    write_json(pack / "manifest.json", curseforge_manifest())
    (pack / "config" / "ftbquests" / "quests").mkdir(parents=True)
    write_jar(
        pack / "mods" / "content.jar",
        {
            "assets/demo/lang/en_us.json": "{}",
            "assets/demo/patchouli_books/book/en_us/entries/start.json": "{}",
            "assets/demo/modonomicon/books/book/en_us/entries/start.json": "{}",
            "assets/ae2/ae2guide/start.md": "# Start",
        },
    )

    result = inspect_modpack(pack)

    assert capability(result, "mod_language").detected
    assert capability(result, "ftb_quests").detected
    assert capability(result, "patchouli").detected
    assert capability(result, "modonomicon").detected
    assert capability(result, "guideme").detected
    assert capability(result, "config_text").detected


def test_inspect_detects_filesystem_language_and_chinese_resources(tmp_path: Path):
    pack = tmp_path / "localized"
    write_json(pack / "manifest.json", curseforge_manifest())
    write_json(pack / "kubejs" / "assets" / "demo" / "lang" / "en_us.json", {})
    write_json(pack / "resourcepacks" / "existing" / "assets" / "demo" / "lang" / "zh_cn.json", {})

    result = inspect_modpack(pack)

    assert capability(result, "mod_language").detected
    assert capability(result, "config_text").detected
    assert result.existing_chinese.status is ChineseResourceStatus.PARTIAL
    assert result.existing_chinese.item_count == 1


def test_inspect_detects_chinese_resource_inside_jar(tmp_path: Path):
    pack = tmp_path / "jar-zh"
    write_jar(
        pack / "mods" / "localized.jar",
        {"assets/demo/lang/zh_cn.json": "{}"},
    )

    result = inspect_modpack(pack)

    assert result.existing_chinese.status is ChineseResourceStatus.PARTIAL
    assert result.existing_chinese.sources == ("mods/localized.jar",)


def test_inspect_bad_jar_is_diagnostic_and_does_not_stop_other_jars(tmp_path: Path):
    pack = tmp_path / "bad-jar"
    mods = pack / "mods"
    mods.mkdir(parents=True)
    (mods / "broken.jar").write_bytes(b"not a zip")
    write_jar(mods / "good.jar", {"assets/demo/lang/en_us.json": "{}"})

    result = inspect_modpack(pack)

    assert result.mod_count == 2
    assert capability(result, "mod_language").detected
    assert any(message.code == "jar_bad_zip" for message in result.messages)


def test_inspect_reports_safe_zip_limit_without_reading_content(tmp_path: Path):
    pack = tmp_path / "limited"
    write_jar(
        pack / "mods" / "many.jar",
        {
            "assets/demo/lang/en_us.json": "{}",
            "assets/demo/guides/start.md": "# Start",
        },
    )
    limits = ZipSafetyLimits(
        max_entries=1,
        max_entry_uncompressed=1024,
        max_candidate_uncompressed_total=2048,
        max_actual_read_total=2048,
        max_compression_ratio=200,
        chunk_size=64,
    )

    result = inspect_modpack(pack, zip_limits=limits)

    assert any(message.code == "jar_entry_count_limit" for message in result.messages)


def test_inspection_never_modifies_jar(tmp_path: Path):
    pack = tmp_path / "readonly"
    jar_path = write_jar(
        pack / "mods" / "demo.jar",
        {"assets/demo/lang/en_us.json": '{"demo": "Hello"}'},
    )
    before = hashlib.sha256(jar_path.read_bytes()).hexdigest()

    inspect_modpack(pack)

    after = hashlib.sha256(jar_path.read_bytes()).hexdigest()
    assert after == before


def test_json_model_omits_user_absolute_path_from_diagnostics_and_evidence(tmp_path: Path):
    pack = tmp_path / "private-user-path" / "pack"
    write_jar(pack / "mods" / "demo.jar")

    result = inspect_modpack(pack)
    payload = json.dumps(result.to_dict(), ensure_ascii=False)

    assert str(tmp_path) not in payload
    assert result.to_dict()["input_directory"] == "pack"
    assert all(not Path(message.location).is_absolute() for message in result.messages)


def test_cli_inspect_text_output(tmp_path: Path, capsys):
    pack = tmp_path / "cli-text"
    write_json(pack / "manifest.json", curseforge_manifest(name="CLI Pack"))

    exit_code = main(["inspect", str(pack)])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "整合包：CLI Pack" in output
    assert "状态：有效" in output
    assert "Minecraft：1.20.1" in output
    assert "加载器：NeoForge 47.1.0" in output


def test_cli_inspect_json_output_uses_model(tmp_path: Path, capsys):
    pack = tmp_path / "cli-json"
    write_json(pack / "manifest.json", curseforge_manifest(name="JSON Pack"))

    exit_code = main(["inspect", str(pack), "--json"])
    raw = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert raw["display_name"] == "JSON Pack"
    assert raw["validity"] == "valid"
    assert raw["loader_name"] == "NeoForge"
    assert raw["can_continue"] is True
    assert raw == inspect_modpack(pack).to_dict() | {"inspection_duration": raw["inspection_duration"]}


@pytest.mark.parametrize(
    ("kind", "expected_code"),
    [
        ("valid", 0),
        ("probable", 0),
        ("invalid", 2),
    ],
)
def test_cli_inspect_exit_codes(tmp_path: Path, capsys, kind: str, expected_code: int):
    pack = tmp_path / kind
    pack.mkdir()
    if kind == "valid":
        write_json(pack / "manifest.json", curseforge_manifest())
    elif kind == "probable":
        (pack / "config").mkdir()

    exit_code = main(["inspect", str(pack), "--json"])
    capsys.readouterr()

    assert exit_code == expected_code


def test_inspect_supports_modrinth_and_packwiz_metadata(tmp_path: Path):
    modrinth = tmp_path / "modrinth"
    write_json(
        modrinth / "modrinth.index.json",
        {
            "name": "Modrinth Pack",
            "dependencies": {
                "minecraft": "1.21",
                "quilt-loader": "0.26.3",
            },
        },
    )
    packwiz = tmp_path / "packwiz"
    packwiz.mkdir()
    (packwiz / "pack.toml").write_text(
        'name = "Packwiz Pack"\n[versions]\nminecraft = "1.20.1"\nforge = "47.2.0"\n',
        encoding="utf-8",
    )

    modrinth_result = inspect_modpack(modrinth)
    packwiz_result = inspect_modpack(packwiz)

    assert modrinth_result.loader == modrinth_result.loader.__class__("Quilt", "0.26.3")
    assert packwiz_result.loader == packwiz_result.loader.__class__("Forge", "47.2.0")


@pytest.mark.parametrize(
    "content",
    [
        "Name=First\nName=Second\n",
        "[broken\nName=Pack\n",
        "this line has no delimiter\n",
    ],
)
def test_broken_instance_cfg_becomes_diagnostic(tmp_path: Path, content: str):
    pack = tmp_path / "broken-instance"
    pack.mkdir()
    (pack / "instance.cfg").write_text(content, encoding="utf-8")

    result = inspect_modpack(pack)

    assert result.validity is InspectionValidity.PROBABLE
    assert any(message.code == "metadata_unreadable" for message in result.messages)


def test_instance_cfg_wrong_encoding_becomes_diagnostic(tmp_path: Path):
    pack = tmp_path / "wrong-encoding"
    pack.mkdir()
    (pack / "instance.cfg").write_bytes(b"Name=\xff\xfe\n")

    result = inspect_modpack(pack)

    assert result.validity is InspectionValidity.PROBABLE
    assert any(message.code == "metadata_unreadable" for message in result.messages)


def test_broken_metadata_does_not_stop_other_metadata(tmp_path: Path):
    pack = tmp_path / "mixed-metadata"
    write_json(pack / "manifest.json", curseforge_manifest(name="Working Manifest"))
    (pack / "instance.cfg").write_text("Name=First\nName=Second\n", encoding="utf-8")

    result = inspect_modpack(pack)

    assert result.validity is InspectionValidity.VALID
    assert result.display_name == "Working Manifest"
    assert any(message.code == "metadata_unreadable" for message in result.messages)
    assert "parsed metadata: manifest.json" in result.evidence


def test_cli_broken_instance_cfg_has_stable_exit_without_traceback(
    tmp_path: Path,
    capsys,
):
    pack = tmp_path / "cli-broken-instance"
    pack.mkdir()
    (pack / "instance.cfg").write_text("Name=First\nName=Second\n", encoding="utf-8")

    exit_code = main(["inspect", str(pack), "--json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["validity"] == "probable"
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err


@pytest.mark.parametrize(
    "unsafe_name",
    [
        r"C:\Users\Private\Pack",
        "C:/Users/Private/Pack",
        r"\\server\share\Private\Pack",
        "/home/Private/Pack",
        "Private\x00Pack",
        "Private\nPack",
    ],
)
def test_unsafe_metadata_name_falls_back_without_leaking(
    tmp_path: Path,
    unsafe_name: str,
):
    pack = tmp_path / "safe-fallback"
    write_json(pack / "manifest.json", curseforge_manifest(name=unsafe_name))

    result = inspect_modpack(pack)
    payload = json.dumps(result.to_dict(), ensure_ascii=False)

    assert result.display_name == "safe-fallback"
    assert any(message.code == "metadata_value_rejected" for message in result.messages)
    assert "Private" not in payload


def test_unsafe_minecraft_and_loader_values_are_rejected(tmp_path: Path):
    minecraft_pack = tmp_path / "unsafe-minecraft"
    write_json(
        minecraft_pack / "manifest.json",
        curseforge_manifest(minecraft=r"C:\Users\Private\version"),
    )
    loader_pack = tmp_path / "unsafe-loader"
    write_json(
        loader_pack / "modrinth.index.json",
        {
            "name": "Loader Test",
            "dependencies": {
                "minecraft": "1.20.1",
                "forge": "/home/Private/loader",
            },
        },
    )

    minecraft_result = inspect_modpack(minecraft_pack)
    loader_result = inspect_modpack(loader_pack)
    payload = json.dumps(
        [minecraft_result.to_dict(), loader_result.to_dict()],
        ensure_ascii=False,
    )

    assert minecraft_result.minecraft_version == "unknown"
    assert loader_result.loader_name == "unknown"
    assert sum(
        message.code == "metadata_value_rejected"
        for result in (minecraft_result, loader_result)
        for message in result.messages
    ) == 2
    assert "Private" not in payload


def test_unsafe_loader_name_is_rejected(tmp_path: Path):
    pack = tmp_path / "unsafe-loader-name"
    write_json(
        pack / "profile.json",
        {
            "name": "Safe Name",
            "game_version": "1.20.1",
            "loader": r"\\server\share\PrivateLoader",
        },
    )

    result = inspect_modpack(pack)
    payload = json.dumps(result.to_dict(), ensure_ascii=False)

    assert result.loader_name == "unknown"
    assert any(message.code == "metadata_value_rejected" for message in result.messages)
    assert "PrivateLoader" not in payload


@pytest.mark.parametrize(
    "value",
    ["混沌夜幕（重制版）", "1.20.1", "47.1.0", "Forge - Recommended"],
)
def test_sanitize_metadata_text_preserves_normal_values(value: str):
    assert sanitize_metadata_text(value) == value


@pytest.mark.parametrize(
    "value",
    [
        r"C:\Users\Private\Pack",
        "C:/Users/Private/Pack",
        r"\\server\share\Pack",
        "/home/private/Pack",
        "bad\x00value",
        "bad\nvalue",
        "bad\rvalue",
        "bad\tvalue",
    ],
)
def test_sanitize_metadata_text_rejects_paths_and_controls(value: str):
    with pytest.raises(ValueError):
        sanitize_metadata_text(value)


def test_json_output_is_stable_across_repeated_inspections(tmp_path: Path):
    pack = tmp_path / "stable"
    write_json(pack / "manifest.json", curseforge_manifest())
    (pack / "config").mkdir()
    (pack / "resourcepacks").mkdir()

    first = inspect_modpack(pack).to_dict()
    second = inspect_modpack(pack).to_dict()
    first.pop("inspection_duration")
    second.pop("inspection_duration")

    assert first == second


def test_model_normalizes_output_order_independent_of_input_order():
    capabilities = [
        ContentCapability("guideme", "GuideME", True),
        ContentCapability("mod_language", "语言", True),
    ]
    messages = [
        InspectionMessage("warning", "z_code", "Z", "z"),
        InspectionMessage("error", "a_code", "A", "a"),
    ]
    common = {
        "input_directory": Path("pack"),
        "validity": InspectionValidity.PROBABLE,
        "display_name": "Pack",
        "minecraft_version": "unknown",
        "loader": LoaderInfo(),
        "mod_count": 0,
        "existing_chinese": ExistingChineseResources(ChineseResourceStatus.NONE),
        "inspection_duration": 0.0,
    }

    first = ModpackInspection(
        capabilities=capabilities,
        messages=messages,
        evidence=["z evidence", "a evidence"],
        **common,
    )
    second = ModpackInspection(
        capabilities=list(reversed(capabilities)),
        messages=list(reversed(messages)),
        evidence=["a evidence", "z evidence"],
        **common,
    )

    assert first.to_dict() == second.to_dict()
    assert [item.key for item in first.capabilities] == ["mod_language", "guideme"]
    assert [item.severity for item in first.messages] == ["error", "warning"]


def test_json_is_stable_across_python_hash_seeds(tmp_path: Path):
    pack = tmp_path / "hash-seed"
    write_json(pack / "manifest.json", curseforge_manifest())
    (pack / "mods").mkdir()
    (pack / "config").mkdir()
    (pack / "resourcepacks").mkdir()
    script = (
        "import json,sys;"
        "from pathlib import Path;"
        "from mc_han.services.modpack_inspector import inspect_modpack;"
        "value=inspect_modpack(Path(sys.argv[1])).to_dict();"
        "value.pop('inspection_duration');"
        "print(json.dumps(value,ensure_ascii=True,sort_keys=True))"
    )

    outputs = []
    project_root = Path(__file__).parents[1]
    for seed in ("1", "98765"):
        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = seed
        environment["PYTHONPATH"] = os.pathsep.join(
            filter(
                None,
                (str(project_root / "src"), environment.get("PYTHONPATH", "")),
            )
        )
        completed = subprocess.run(
            [sys.executable, "-c", script, str(pack)],
            cwd=project_root,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        outputs.append(completed.stdout)

    assert outputs[0] == outputs[1]


def test_existing_chinese_status_none_when_inspection_is_complete(tmp_path: Path):
    pack = tmp_path / "no-chinese"
    write_jar(pack / "mods" / "safe.jar", {"assets/demo/lang/en_us.json": "{}"})

    result = inspect_modpack(pack)

    assert result.existing_chinese.status is ChineseResourceStatus.NONE


def test_existing_chinese_status_unknown_for_bad_jar(tmp_path: Path):
    pack = tmp_path / "bad-only"
    (pack / "mods").mkdir(parents=True)
    (pack / "mods" / "broken.jar").write_bytes(b"not a ZIP")

    result = inspect_modpack(pack)

    assert result.existing_chinese.status is ChineseResourceStatus.UNKNOWN


def test_existing_chinese_status_unknown_for_safety_limit(tmp_path: Path):
    pack = tmp_path / "limited-chinese-check"
    write_jar(
        pack / "mods" / "limited.jar",
        {
            "assets/demo/lang/en_us.json": "{}",
            "assets/demo/guides/page.md": "# Page",
        },
    )
    limits = ZipSafetyLimits(
        max_entries=1,
        max_entry_uncompressed=1024,
        max_candidate_uncompressed_total=2048,
        max_actual_read_total=2048,
        max_compression_ratio=200,
        chunk_size=64,
    )

    result = inspect_modpack(pack, zip_limits=limits)

    assert result.existing_chinese.status is ChineseResourceStatus.UNKNOWN


def test_existing_chinese_remains_partial_when_another_jar_is_bad(tmp_path: Path):
    pack = tmp_path / "partial-with-bad"
    write_jar(
        pack / "mods" / "localized.jar",
        {"assets/demo/lang/zh_cn.json": "{}"},
    )
    (pack / "mods" / "broken.jar").write_bytes(b"not a ZIP")

    result = inspect_modpack(pack)

    assert result.existing_chinese.status is ChineseResourceStatus.PARTIAL
    assert any(message.code == "jar_bad_zip" for message in result.messages)


def test_cli_and_json_use_same_chinese_status(tmp_path: Path, capsys):
    pack = tmp_path / "cli-chinese-unknown"
    (pack / "mods").mkdir(parents=True)
    (pack / "mods" / "broken.jar").write_bytes(b"not a ZIP")

    text_exit = main(["inspect", str(pack)])
    text_output = capsys.readouterr().out
    json_exit = main(["inspect", str(pack), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert text_exit == json_exit == 0
    assert "已有中文：无法完整判断" in text_output
    assert payload["existing_chinese"]["status"] == "unknown"


@pytest.mark.parametrize(
    ("entry_name", "expected_text", "expected_status"),
    [
        ("assets/demo/lang/en_us.json", "未发现", "none"),
        ("assets/demo/lang/zh_cn.json", "部分存在", "partial"),
    ],
)
def test_cli_chinese_status_labels(
    tmp_path: Path,
    capsys,
    entry_name: str,
    expected_text: str,
    expected_status: str,
):
    pack = tmp_path / expected_status
    write_jar(pack / "mods" / "content.jar", {entry_name: "{}"})

    main(["inspect", str(pack)])
    text_output = capsys.readouterr().out
    main(["inspect", str(pack), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert f"已有中文：{expected_text}" in text_output
    assert payload["existing_chinese"]["status"] == expected_status


def _symlink_or_skip(link: Path, target: Path, *, is_directory: bool) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(target, target_is_directory=is_directory)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"symbolic links are unavailable: {type(error).__name__}")


@pytest.mark.parametrize("filename", ["manifest.json", "instance.cfg"])
def test_external_metadata_symlink_is_not_instance_evidence(
    tmp_path: Path,
    filename: str,
):
    outside = tmp_path / "outside" / filename
    outside.parent.mkdir()
    if filename == "manifest.json":
        outside.write_text(json.dumps(curseforge_manifest()), encoding="utf-8")
    else:
        outside.write_text("Name=Outside\n", encoding="utf-8")
    pack = tmp_path / "linked-metadata"
    pack.mkdir()
    _symlink_or_skip(pack / filename, outside, is_directory=False)

    result = inspect_modpack(pack)
    payload = json.dumps(result.to_dict(), ensure_ascii=False)

    assert result.validity is InspectionValidity.INVALID
    assert not any(item.startswith("parsed metadata:") for item in result.evidence)
    assert str(outside) not in payload


@pytest.mark.parametrize("dirname", ["mods", "config", "resourcepacks", "kubejs"])
def test_external_marker_symlink_does_not_make_directory_probable(
    tmp_path: Path,
    dirname: str,
):
    outside = tmp_path / f"outside-{dirname}"
    outside.mkdir()
    if dirname == "mods":
        write_jar(outside / "outside.jar")
    pack = tmp_path / f"linked-{dirname}"
    pack.mkdir()
    _symlink_or_skip(pack / dirname, outside, is_directory=True)

    result = inspect_modpack(pack)
    payload = json.dumps(result.to_dict(), ensure_ascii=False)

    assert result.validity is InspectionValidity.INVALID
    assert f"directory marker: {dirname}/" not in result.evidence
    assert str(outside) not in payload


def test_capability_counts_have_consistent_semantics(tmp_path: Path):
    pack = tmp_path / "capability-counts"
    (pack / "config" / "ftbquests" / "quests").mkdir(parents=True)
    write_jar(
        pack / "mods" / "patchouli.jar",
        {
            f"assets/demo/patchouli_books/book/en_us/entries/{index}.json": "{}"
            for index in range(10)
        },
    )
    write_jar(
        pack / "mods" / "guide-one.jar",
        {"assets/demo/guides/one.md": "# One"},
    )
    write_jar(
        pack / "mods" / "guide-two.jar",
        {"assets/demo/ae2guide/two.md": "# Two"},
    )

    result = inspect_modpack(pack)
    ftb = capability(result, "ftb_quests")
    patchouli = capability(result, "patchouli")
    guideme = capability(result, "guideme")

    assert ftb.detected and ftb.item_count == 0 and ftb.source_count == 0
    assert patchouli.item_count == 10 and patchouli.source_count == 1
    assert guideme.item_count == 2 and guideme.source_count == 2
    assert "count" not in patchouli.to_dict()
    assert "available" not in patchouli.to_dict()


def test_models_are_deeply_immutable_and_to_dict_is_detached():
    source_values = ["mods/demo.jar"]
    capability_values = [
        ContentCapability(
            "mod_language",
            "语言",
            True,
            item_count=1,
            source_count=1,
            sources=source_values,
        )
    ]
    message_values = [InspectionMessage("warning", "test", "Test")]
    evidence_values = ["evidence"]
    inspection = ModpackInspection(
        input_directory=Path("pack"),
        validity=InspectionValidity.PROBABLE,
        display_name="Pack",
        minecraft_version="unknown",
        loader=LoaderInfo(),
        mod_count=1,
        capabilities=capability_values,
        existing_chinese=ExistingChineseResources(ChineseResourceStatus.NONE),
        messages=message_values,
        evidence=evidence_values,
        inspection_duration=0.0,
    )

    source_values.append("mods/other.jar")
    capability_values.clear()
    message_values.clear()
    evidence_values.clear()
    payload = inspection.to_dict()
    payload["capabilities"][0]["sources"].append("changed")
    payload["messages"].clear()
    payload["evidence"].append("changed")

    assert inspection.capabilities[0].sources == ("mods/demo.jar",)
    assert len(inspection.capabilities) == len(inspection.messages) == 1
    assert inspection.evidence == ("evidence",)
    with pytest.raises(FrozenInstanceError):
        inspection.mod_count = 2  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("loader", "not-loader"),
        ("capabilities", ["not-capability"]),
        ("messages", ["not-message"]),
        ("existing_chinese", "not-Chinese-resources"),
    ],
)
def test_model_rejects_invalid_nested_types(field: str, value: object):
    values = {
        "input_directory": Path("pack"),
        "validity": InspectionValidity.PROBABLE,
        "display_name": "Pack",
        "minecraft_version": "unknown",
        "loader": LoaderInfo(),
        "mod_count": 0,
        "capabilities": [],
        "existing_chinese": ExistingChineseResources(ChineseResourceStatus.NONE),
        "messages": [],
        "evidence": [],
        "inspection_duration": 0.0,
    }
    values[field] = value

    with pytest.raises(TypeError):
        ModpackInspection(**values)


@pytest.mark.parametrize("error_type", [PermissionError, RuntimeError])
def test_initial_resolve_error_returns_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[Exception],
):
    pack = tmp_path / "private-root"
    pack.mkdir()

    def fail_resolve(self: Path, strict: bool = False) -> Path:
        raise error_type("private absolute path")

    monkeypatch.setattr(Path, "resolve", fail_resolve)

    result = inspect_modpack(pack)
    payload = json.dumps(result.to_dict(), ensure_ascii=False)

    assert result.validity is InspectionValidity.INVALID
    assert result.can_continue is False
    assert [message.code for message in result.messages] == ["directory_unreadable"]
    assert str(tmp_path) not in payload
    assert "private absolute path" not in payload


def test_missing_initial_directory_is_stable_unreadable(tmp_path: Path):
    pack = tmp_path / "deleted-before-inspection"

    result = inspect_modpack(pack)

    assert result.validity is InspectionValidity.INVALID
    assert result.can_continue is False
    assert [message.code for message in result.messages] == ["directory_unreadable"]


def test_metadata_lstat_permission_error_does_not_stop_other_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    pack = tmp_path / "metadata-permission"
    write_json(pack / "manifest.json", curseforge_manifest(name="Unreadable"))
    write_json(
        pack / "mmc-pack.json",
        {
            "components": [
                {"uid": "net.minecraft", "version": "1.20.4"},
                {"uid": "net.fabricmc.fabric-loader", "version": "0.15.11"},
            ]
        },
    )
    original_lstat = Path.lstat

    def guarded_lstat(self: Path):
        if self == pack / "manifest.json":
            raise PermissionError("private absolute path")
        return original_lstat(self)

    monkeypatch.setattr(Path, "lstat", guarded_lstat)

    result = inspect_modpack(pack)
    payload = json.dumps(result.to_dict(), ensure_ascii=False)

    assert result.validity is InspectionValidity.VALID
    assert result.minecraft_version == "1.20.4"
    assert result.loader_name == "Fabric"
    assert any(
        message.code == "path_unreadable" and message.location == "manifest.json"
        for message in result.messages
    )
    assert str(tmp_path) not in payload
    assert "private absolute path" not in payload


def test_marker_directory_oserror_is_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    pack = tmp_path / "marker-permission"
    write_json(pack / "manifest.json", curseforge_manifest())
    (pack / "config").mkdir()
    original_is_dir = Path.is_dir

    def guarded_is_dir(self: Path) -> bool:
        if self == pack / "config":
            raise OSError("private absolute path")
        return original_is_dir(self)

    monkeypatch.setattr(Path, "is_dir", guarded_is_dir)

    result = inspect_modpack(pack)

    assert result.validity is InspectionValidity.VALID
    assert any(
        message.code == "path_unreadable" and message.location == "config"
        for message in result.messages
    )


def test_jar_deleted_during_inspection_is_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    pack = tmp_path / "vanishing-jar"
    jar_path = write_jar(
        pack / "mods" / "vanishing.jar",
        {"assets/demo/lang/en_us.json": "{}"},
    )
    original_zip_file = zipfile.ZipFile

    def deleting_zip_file(path, *args, **kwargs):
        Path(path).unlink()
        return original_zip_file(path, *args, **kwargs)

    monkeypatch.setattr(inspector_module.zipfile, "ZipFile", deleting_zip_file)

    result = inspect_modpack(pack)

    assert not jar_path.exists()
    assert result.mod_count == 1
    assert any(message.code == "jar_read_error" for message in result.messages)


def test_modpack_deleted_during_inspection_returns_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    pack = tmp_path / "vanishing-pack"
    pack.mkdir()
    (pack / "config").mkdir()
    original_iterdir = Path.iterdir
    removed = False

    def deleting_iterdir(self: Path):
        nonlocal removed
        items = list(original_iterdir(self))
        if self == pack and not removed:
            removed = True
            shutil.rmtree(pack)
        return iter(items)

    monkeypatch.setattr(Path, "iterdir", deleting_iterdir)

    result = inspect_modpack(pack)

    assert result.validity is InspectionValidity.INVALID
    assert result.can_continue is False


def test_cli_handles_initial_resolve_error_as_text_and_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
):
    pack = tmp_path / "cli-private-root"
    pack.mkdir()

    def fail_resolve(self: Path, strict: bool = False) -> Path:
        raise PermissionError("private absolute path")

    monkeypatch.setattr(Path, "resolve", fail_resolve)

    text_exit = main(["inspect", str(pack)])
    text_capture = capsys.readouterr()
    json_exit = main(["inspect", str(pack), "--json"])
    json_capture = capsys.readouterr()
    payload = json.loads(json_capture.out)

    assert text_exit == json_exit == 2
    assert "Traceback" not in text_capture.out + text_capture.err
    assert "Traceback" not in json_capture.out + json_capture.err
    assert payload["validity"] == "invalid"
    assert payload["can_continue"] is False
    assert str(tmp_path) not in json_capture.out


@pytest.mark.parametrize(
    "kwargs",
    [
        {"detected": False, "item_count": 1, "source_count": 1, "sources": ("a",)},
        {"detected": False, "item_count": 0, "source_count": 1, "sources": ("a",)},
        {"detected": True, "item_count": 0, "source_count": 1, "sources": ("a",)},
        {"detected": True, "item_count": 1, "source_count": 2, "sources": ("a", "b")},
        {"detected": True, "item_count": 2, "source_count": 2, "sources": ("a", "a")},
        {"detected": True, "item_count": 1, "source_count": 1, "sources": ("",)},
    ],
)
def test_content_capability_rejects_inconsistent_values(kwargs: dict[str, object]):
    with pytest.raises(ValueError):
        ContentCapability(key="demo", label="Demo", **kwargs)


@pytest.mark.parametrize("detected", [1, 0, "true"])
def test_content_capability_detected_requires_strict_bool(detected: object):
    with pytest.raises(TypeError):
        ContentCapability(key="demo", label="Demo", detected=detected)


@pytest.mark.parametrize(
    ("field", "value", "error_type"),
    [
        ("item_count", True, TypeError),
        ("source_count", False, TypeError),
        ("item_count", -1, ValueError),
        ("source_count", -1, ValueError),
    ],
)
def test_content_capability_counts_are_nonnegative_strict_ints(
    field: str,
    value: object,
    error_type: type[Exception],
):
    kwargs = {
        "key": "demo",
        "label": "Demo",
        "detected": True,
        "item_count": 0,
        "source_count": 0,
        "sources": (),
    }
    kwargs[field] = value

    with pytest.raises(error_type):
        ContentCapability(**kwargs)


def test_content_capability_allows_empty_detected_source():
    capability_value = ContentCapability(
        key="ftb_quests",
        label="FTB Quests",
        detected=True,
    )

    assert capability_value.detected is True
    assert capability_value.item_count == capability_value.source_count == 0
    assert capability_value.sources == ()


def test_content_capability_allows_multiple_items_in_one_source():
    sources = ["mods/demo.jar"]
    capability_value = ContentCapability(
        key="patchouli",
        label="Patchouli",
        detected=True,
        item_count=2,
        source_count=1,
        sources=sources,
    )
    sources.append("mods/other.jar")

    assert capability_value.item_count == 2
    assert capability_value.source_count == 1
    assert capability_value.sources == ("mods/demo.jar",)


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "status": ChineseResourceStatus.PARTIAL,
            "item_count": 0,
            "source_count": 0,
            "sources": (),
        },
        {
            "status": ChineseResourceStatus.NONE,
            "item_count": 1,
            "source_count": 1,
            "sources": ("a",),
        },
        {
            "status": ChineseResourceStatus.UNKNOWN,
            "item_count": 1,
            "source_count": 1,
            "sources": ("a",),
        },
        {
            "status": ChineseResourceStatus.PARTIAL,
            "item_count": 1,
            "source_count": 2,
            "sources": ("a", "b"),
        },
        {
            "status": ChineseResourceStatus.PARTIAL,
            "item_count": 2,
            "source_count": 1,
            "sources": ("a", "b"),
        },
    ],
)
def test_existing_chinese_rejects_inconsistent_values(kwargs: dict[str, object]):
    with pytest.raises(ValueError):
        ExistingChineseResources(**kwargs)


@pytest.mark.parametrize(
    ("field", "value", "error_type"),
    [
        ("item_count", True, TypeError),
        ("source_count", False, TypeError),
        ("item_count", -1, ValueError),
        ("source_count", -1, ValueError),
    ],
)
def test_existing_chinese_counts_are_nonnegative_strict_ints(
    field: str,
    value: object,
    error_type: type[Exception],
):
    kwargs = {
        "status": ChineseResourceStatus.NONE,
        "item_count": 0,
        "source_count": 0,
        "sources": (),
    }
    kwargs[field] = value

    with pytest.raises(error_type):
        ExistingChineseResources(**kwargs)


@pytest.mark.parametrize(
    "status",
    [ChineseResourceStatus.NONE, ChineseResourceStatus.UNKNOWN],
)
def test_existing_chinese_allows_empty_nonpartial_status(
    status: ChineseResourceStatus,
):
    value = ExistingChineseResources(status)

    assert value.item_count == value.source_count == 0
    assert value.sources == ()


def test_existing_chinese_partial_is_consistent_and_detached():
    sources = ["mods/localized.jar"]
    value = ExistingChineseResources(
        ChineseResourceStatus.PARTIAL,
        item_count=1,
        source_count=1,
        sources=sources,
    )
    sources.append("mods/other.jar")

    assert value.item_count == value.source_count == 1
    assert value.sources == ("mods/localized.jar",)
