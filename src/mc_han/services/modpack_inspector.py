from __future__ import annotations

import configparser
import json
import math
import os
import stat
import tomllib
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from time import perf_counter
from typing import Any

from mc_han.utils.safe_paths import (
    UnsafePathError,
    parse_untrusted_relative_path,
    resolve_path_for_operation,
)
from mc_han.utils.safe_zip import (
    BAD_ZIP,
    DEFAULT_ZIP_LIMITS,
    SafeZipReader,
    ZipDiagnostic,
    ZipSafetyLimits,
)
from mc_han.workflow.models import (
    CAPABILITY_ORDER,
    ChineseResourceStatus,
    ContentCapability,
    ExistingChineseResources,
    InspectionMessage,
    InspectionValidity,
    LoaderInfo,
    ModpackInspection,
)

MAX_METADATA_BYTES = 1024 * 1024
MAX_JAR_DIAGNOSTIC_MESSAGES = 50
UNKNOWN = "unknown"

CAPABILITY_LABELS = {
    "mod_language": "模组语言文件",
    "ftb_quests": "FTB Quests",
    "patchouli": "Patchouli",
    "modonomicon": "Modonomicon",
    "guideme": "GuideME / AE2 指南",
    "config_text": "配置与脚本语言文本",
}

METADATA_FILES = (
    "minecraftinstance.json",
    "manifest.json",
    "mmc-pack.json",
    "instance.cfg",
    "modrinth.index.json",
    "profile.json",
    "pack.toml",
    "PCL/Setup.ini",
)

INSTANCE_MARKERS = ("mods", "config", "resourcepacks", "kubejs")
MIN_VALID_JARS_FOR_INSTANCE = 10


@dataclass(frozen=True)
class _ValueEvidence:
    value: str
    source: str
    priority: int


@dataclass(frozen=True)
class _LoaderEvidence:
    name: str
    version: str
    source: str


@dataclass(frozen=True)
class _RejectedMetadataValue:
    source: str
    field: str


@dataclass
class _MetadataFacts:
    names: list[_ValueEvidence] = field(default_factory=list)
    minecraft_versions: list[_ValueEvidence] = field(default_factory=list)
    loaders: list[_LoaderEvidence] = field(default_factory=list)
    unknown_loader_ids: list[tuple[str, str]] = field(default_factory=list)
    files_found: list[str] = field(default_factory=list)
    files_parsed: list[str] = field(default_factory=list)
    rejected_values: list[_RejectedMetadataValue] = field(default_factory=list)


@dataclass
class _CapabilityObservation:
    detected: bool = False
    item_count: int = 0
    sources: set[str] = field(default_factory=set)


def inspect_modpack(
    path: str | os.PathLike[str],
    *,
    zip_limits: ZipSafetyLimits = DEFAULT_ZIP_LIMITS,
) -> ModpackInspection:
    started_at = perf_counter()
    input_path = Path(path)
    fallback_name = _safe_fallback_name(input_path.name)
    try:
        input_path = input_path.expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        return _invalid_inspection(
            input_path,
            display_name=fallback_name,
            started_at=started_at,
            code="directory_unreadable",
            message="无法读取所选目录。",
        )
    fallback_name = _safe_fallback_name(input_path.name)

    try:
        path_metadata = input_path.stat()
    except (OSError, RuntimeError):
        return _invalid_inspection(
            input_path,
            display_name=fallback_name,
            started_at=started_at,
            code="directory_unreadable",
            message="无法读取所选目录。",
        )
    if not stat.S_ISDIR(path_metadata.st_mode):
        return _invalid_inspection(
            input_path,
            display_name=fallback_name,
            started_at=started_at,
            code="not_a_directory",
            message="所选路径不是目录。",
        )
    try:
        next(input_path.iterdir(), None)
    except (OSError, RuntimeError):
        return _invalid_inspection(
            input_path,
            display_name=fallback_name,
            started_at=started_at,
            code="directory_unreadable",
            message="无法读取所选目录。",
        )

    messages: list[InspectionMessage] = []
    evidence: list[str] = []
    facts = _inspect_metadata(input_path, messages, evidence)
    markers = _detect_instance_markers(input_path, messages)
    evidence.extend(f"directory marker: {marker}/" for marker in sorted(markers))

    wrong_level = _looks_like_selected_mods_directory(input_path)
    if wrong_level:
        messages.append(
            InspectionMessage(
                severity="warning",
                code="wrong_directory_level",
                message="当前目录看起来是 mods 子目录，请改选它的上一级整合包目录。",
                location=".",
            )
        )
        evidence.append("directory name and contents resemble a selected mods/ subdirectory")

    jar_paths = _discover_mod_jars(input_path, messages)
    mod_count = len(jar_paths)
    if mod_count:
        evidence.append(f"mods/*.jar: {mod_count}")

    capability_results = {
        key: _CapabilityObservation()
        for key in CAPABILITY_ORDER
    }
    chinese_count = 0
    chinese_sources: set[str] = set()
    jar_inspection_incomplete = False

    filesystem_results = _inspect_filesystem_sources(input_path, messages)
    for key, observation in filesystem_results.items():
        if key == "existing_chinese":
            chinese_count += observation.item_count
            chinese_sources.update(observation.sources)
            continue
        aggregate = capability_results[key]
        aggregate.detected = aggregate.detected or observation.detected
        aggregate.item_count += observation.item_count
        aggregate.sources.update(observation.sources)

    jar_diagnostic_count = 0
    jar_diagnostic_messages = 0
    valid_jar_count = 0
    for jar_path in jar_paths:
        result = _inspect_jar_capabilities(
            input_path,
            jar_path,
            zip_limits=zip_limits,
        )
        if result.readable:
            valid_jar_count += 1
        for key, count in result.capability_counts.items():
            aggregate = capability_results[key]
            aggregate.detected = True
            aggregate.item_count += count
            aggregate.sources.add(result.relative_jar)
        chinese_count += result.chinese_count
        if result.chinese_count:
            chinese_sources.add(result.relative_jar)
        if result.diagnostics or result.unsafe_entry_seen:
            jar_inspection_incomplete = True
        for diagnostic in result.diagnostics:
            jar_diagnostic_count += 1
            if jar_diagnostic_messages >= MAX_JAR_DIAGNOSTIC_MESSAGES:
                continue
            messages.append(_message_from_zip_diagnostic(result.relative_jar, diagnostic))
            jar_diagnostic_messages += 1
        if result.unsafe_entry_seen:
            jar_diagnostic_count += 1
            if jar_diagnostic_messages < MAX_JAR_DIAGNOSTIC_MESSAGES:
                messages.append(
                    InspectionMessage(
                        severity="warning",
                        code="unsafe_jar_entry",
                        message="JAR 包含不安全的 entry 路径，已跳过。",
                        location=result.relative_jar,
                    )
                )
                jar_diagnostic_messages += 1

    if jar_diagnostic_count > jar_diagnostic_messages:
        messages.append(
            InspectionMessage(
                severity="warning",
                code="jar_diagnostics_omitted",
                message=f"另有 {jar_diagnostic_count - jar_diagnostic_messages} 条 JAR 诊断未展开显示。",
                location="mods",
            )
        )
    if jar_diagnostic_count:
        evidence.append(f"JAR safety/read diagnostics: {jar_diagnostic_count}")

    _append_rejected_metadata_messages(facts, messages)
    display_name = _resolve_display_name(facts, fallback_name, messages, evidence)
    minecraft_version = _resolve_minecraft_version(facts, messages, evidence)
    loader = _resolve_loader(facts, messages, evidence)
    validity = _resolve_validity(
        facts=facts,
        markers=markers,
        mod_count=mod_count,
        valid_jar_count=valid_jar_count,
        wrong_level=wrong_level,
    )

    if validity is InspectionValidity.INVALID:
        messages.append(
            InspectionMessage(
                severity="error",
                code="no_instance_evidence",
                message="所选目录中没有发现 Minecraft 整合包或实例迹象。",
                location=".",
            )
        )
    if validity is InspectionValidity.PROBABLE:
        messages.append(
            InspectionMessage(
                severity="warning",
                code="incomplete_instance_evidence",
                message="发现 Minecraft 实例迹象，但元数据不足；可以继续，但请确认选择的是整合包根目录。",
                location=".",
            )
        )
    if validity is not InspectionValidity.INVALID and minecraft_version == UNKNOWN:
        messages.append(
            InspectionMessage(
                severity="warning",
                code="minecraft_version_unknown",
                message="无法从已知元数据识别 Minecraft 版本。",
                location=".",
            )
        )
    if validity is not InspectionValidity.INVALID and loader.name == UNKNOWN:
        messages.append(
            InspectionMessage(
                severity="warning",
                code="loader_unknown",
                message="无法从已知元数据识别模组加载器。",
                location=".",
            )
        )

    capabilities = tuple(
        ContentCapability(
            key=key,
            label=CAPABILITY_LABELS[key],
            detected=capability_results[key].detected,
            item_count=capability_results[key].item_count,
            source_count=len(capability_results[key].sources),
            sources=tuple(capability_results[key].sources),
        )
        for key in CAPABILITY_ORDER
    )
    chinese_status = (
        ChineseResourceStatus.PARTIAL
        if chinese_count
        else ChineseResourceStatus.UNKNOWN
        if jar_inspection_incomplete
        else ChineseResourceStatus.NONE
    )
    existing_chinese = ExistingChineseResources(
        status=chinese_status,
        item_count=chinese_count,
        source_count=len(chinese_sources),
        sources=tuple(chinese_sources),
    )
    return ModpackInspection(
        input_directory=input_path,
        validity=validity,
        display_name=display_name,
        minecraft_version=minecraft_version,
        loader=loader,
        mod_count=mod_count,
        capabilities=capabilities,
        existing_chinese=existing_chinese,
        messages=tuple(_deduplicate_messages(messages)),
        evidence=tuple(evidence),
        inspection_duration=perf_counter() - started_at,
    )


@dataclass(frozen=True)
class _JarCapabilityResult:
    relative_jar: str
    capability_counts: Counter[str]
    chinese_count: int
    diagnostics: tuple[ZipDiagnostic, ...]
    unsafe_entry_seen: bool
    readable: bool


def _inspect_jar_capabilities(
    root: Path,
    jar_path: Path,
    *,
    zip_limits: ZipSafetyLimits,
) -> _JarCapabilityResult:
    relative_jar = jar_path.relative_to(root).as_posix()
    counts: Counter[str] = Counter()
    chinese_count = 0
    diagnostics: list[ZipDiagnostic] = []
    unsafe_entry_seen = False
    readable = False
    try:
        safe_jar = resolve_path_for_operation(
            root,
            relative_jar,
            label="mod JAR",
            allowed_top_levels={"mods"},
        )
        with zipfile.ZipFile(safe_jar) as archive:
            readable = True
            reader: SafeZipReader[tuple[str, ...]] = SafeZipReader(archive, limits=zip_limits)

            def select_entry(name: str) -> tuple[str, ...] | None:
                nonlocal unsafe_entry_seen
                try:
                    tags = _classify_capability_entry(name)
                except UnsafePathError:
                    unsafe_entry_seen = True
                    return None
                return tags or None

            for _info, tags in reader.iter_candidates(select_entry):
                for tag in tags:
                    if tag == "existing_chinese":
                        chinese_count += 1
                    else:
                        counts[tag] += 1
            diagnostics.extend(reader.diagnostics)
    except (zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        diagnostics.append(
            ZipDiagnostic(
                code=BAD_ZIP,
                entry=None,
                reason=f"cannot open ZIP archive: {type(error).__name__}",
                stops_jar=True,
            )
        )
    except (OSError, RuntimeError, UnsafePathError) as error:
        diagnostics.append(
            ZipDiagnostic(
                code="read_error",
                entry=None,
                reason=f"cannot inspect ZIP archive: {type(error).__name__}",
                stops_jar=True,
            )
        )
    return _JarCapabilityResult(
        relative_jar=relative_jar,
        capability_counts=counts,
        chinese_count=chinese_count,
        diagnostics=tuple(diagnostics),
        unsafe_entry_seen=unsafe_entry_seen,
        readable=readable,
    )


def _classify_capability_entry(name: str) -> tuple[str, ...]:
    safe_path = parse_untrusted_relative_path(name, label="JAR entry")
    parts = tuple(part.lower() for part in safe_path.parts)
    if not parts or parts[0] != "assets":
        return ()
    lower_name = safe_path.as_posix().lower()
    tags: list[str] = []

    if lower_name.endswith("/lang/en_us.json"):
        tags.append("mod_language")
    if "patchouli_books" in parts and "en_us" in parts and lower_name.endswith(".json"):
        tags.append("patchouli")
    if (
        "modonomicon" in parts
        and "books" in parts
        and "en_us" in parts
        and lower_name.endswith(".json")
    ):
        tags.append("modonomicon")
    if lower_name.endswith(".md") and ("ae2guide" in parts or "guides" in parts):
        tags.append("guideme")
    if (
        lower_name.endswith("/lang/zh_cn.json")
        or ("zh_cn" in parts and "patchouli_books" in parts and lower_name.endswith(".json"))
        or ("zh_cn" in parts and "modonomicon" in parts and lower_name.endswith(".json"))
        or ("zh_cn" in parts and lower_name.endswith(".md") and ("ae2guide" in parts or "guides" in parts))
    ):
        tags.append("existing_chinese")
    return tuple(tags)


def _inspect_filesystem_sources(
    root: Path,
    messages: list[InspectionMessage],
) -> dict[str, _CapabilityObservation]:
    results: dict[str, _CapabilityObservation] = defaultdict(_CapabilityObservation)
    ftb_root = _safe_known_path(root, "config/ftbquests/quests", messages)
    try:
        has_ftb_root = ftb_root is not None and ftb_root.is_dir()
    except (OSError, RuntimeError) as error:
        _append_path_unreadable(messages, "config/ftbquests/quests", error)
        has_ftb_root = False
    if has_ftb_root:
        results["ftb_quests"].detected = True
        results["config_text"].detected = True
        ftb_files = {
            relative
            for relative in _glob_safe_files(
                root,
                "config/ftbquests/quests/**/*",
                messages,
            )
            if _is_ftbquest_text_source(relative)
        }
        _add_filesystem_items(results["ftb_quests"], ftb_files)
        _add_filesystem_items(results["config_text"], ftb_files)

    kubejs_sources = _glob_safe_files(
        root,
        "kubejs/assets/**/lang/en_us.json",
        messages,
    )
    _add_filesystem_items(results["mod_language"], kubejs_sources)
    _add_filesystem_items(results["config_text"], kubejs_sources)
    resourcepack_sources = _glob_safe_files(
        root,
        "resourcepacks/**/assets/**/lang/en_us.json",
        messages,
    )
    _add_filesystem_items(results["mod_language"], resourcepack_sources)
    for pattern in (
        "kubejs/assets/**/lang/zh_cn.json",
        "resourcepacks/**/assets/**/lang/zh_cn.json",
        "config/ftbquests/quests/lang/zh_cn*",
    ):
        _add_filesystem_items(
            results["existing_chinese"],
            _glob_safe_files(root, pattern, messages),
        )
    return results


def _add_filesystem_items(
    observation: _CapabilityObservation,
    sources: set[str],
) -> None:
    if not sources:
        return
    observation.detected = True
    observation.item_count += len(sources)
    observation.sources.update(sources)


def _is_ftbquest_text_source(relative: str) -> bool:
    parts = PurePosixPath(relative).parts
    lower_parts = tuple(part.lower() for part in parts)
    try:
        quests_index = lower_parts.index("quests")
    except ValueError:
        return False
    tail = lower_parts[quests_index + 1 :]
    if not tail:
        return False
    if "lang" not in tail:
        return lower_parts[-1].endswith(".snbt")
    lang_index = tail.index("lang")
    language_path = tail[lang_index + 1 :]
    if not language_path:
        return False
    return language_path[0] == "en_us" or language_path[0].startswith("en_us.")


def _inspect_metadata(
    root: Path,
    messages: list[InspectionMessage],
    evidence: list[str],
) -> _MetadataFacts:
    facts = _MetadataFacts()
    filenames = (*METADATA_FILES, f"{root.name}.json")
    for filename in dict.fromkeys(filenames):
        candidate = _safe_known_path(root, filename, messages)
        if candidate is None:
            continue
        try:
            if not candidate.is_file():
                continue
        except (OSError, RuntimeError) as error:
            _append_metadata_unreadable(messages, filename, error)
            continue
        facts.files_found.append(filename)
        try:
            text = _read_small_metadata_file(root, filename)
            if filename == "instance.cfg":
                _parse_instance_cfg(text, filename, facts)
            elif filename == "pack.toml":
                _parse_pack_toml(text, filename, facts)
            elif filename == "PCL/Setup.ini":
                _parse_pcl_setup(text, filename, facts)
            else:
                raw = json.loads(text)
                if not isinstance(raw, dict):
                    raise ValueError("metadata root is not an object")
                _parse_json_metadata(filename, raw, facts)
            facts.files_parsed.append(filename)
            evidence.append(f"parsed metadata: {filename}")
        except (
            configparser.Error,
            OSError,
            UnicodeDecodeError,
            ValueError,
            json.JSONDecodeError,
            tomllib.TOMLDecodeError,
        ) as error:
            _append_metadata_unreadable(messages, filename, error)
    return facts


def _append_metadata_unreadable(
    messages: list[InspectionMessage],
    filename: str,
    error: OSError | RuntimeError | configparser.Error | ValueError,
) -> None:
    messages.append(
        InspectionMessage(
            severity="warning",
            code="metadata_unreadable",
            message=f"无法解析实例元数据：{type(error).__name__}。",
            location=filename,
        )
    )


def _read_small_metadata_file(root: Path, filename: str) -> str:
    path = resolve_path_for_operation(root, filename, label="instance metadata")
    with path.open("rb") as file:
        data = file.read(MAX_METADATA_BYTES + 1)
    if len(data) > MAX_METADATA_BYTES:
        raise ValueError("metadata file exceeds size limit")
    return data.decode("utf-8-sig")


def _parse_json_metadata(filename: str, raw: dict[str, Any], facts: _MetadataFacts) -> None:
    if filename == "manifest.json":
        minecraft = raw.get("minecraft")
        if not isinstance(minecraft, dict):
            raise ValueError("not a CurseForge manifest")
        _add_name(facts, raw.get("name"), filename, priority=20)
        _add_minecraft_version(facts, minecraft.get("version"), filename)
        loaders = minecraft.get("modLoaders")
        if isinstance(loaders, list):
            for loader in loaders:
                if isinstance(loader, dict):
                    _add_loader_identifier(facts, loader.get("id"), filename)
        return

    if filename == "minecraftinstance.json":
        _add_name(facts, raw.get("name"), filename, priority=10)
        installed = raw.get("installedModpack")
        if isinstance(installed, dict):
            _add_name(facts, installed.get("name"), filename, priority=5)
        _add_minecraft_version(
            facts,
            raw.get("gameVersion") or raw.get("minecraftVersion"),
            filename,
        )
        base_loader = raw.get("baseModLoader")
        if isinstance(base_loader, dict):
            identifier = (
                base_loader.get("name")
                or base_loader.get("type")
                or base_loader.get("modLoaderType")
            )
            version = (
                base_loader.get("forgeVersion")
                or base_loader.get("loaderVersion")
                or base_loader.get("version")
            )
            _add_loader_identifier(facts, identifier, filename, explicit_version=version)
        else:
            _add_loader_identifier(facts, raw.get("modLoader"), filename)
        return

    if filename == "mmc-pack.json":
        components = raw.get("components")
        if not isinstance(components, list):
            raise ValueError("not a Prism/MultiMC component manifest")
        for component in components:
            if not isinstance(component, dict):
                continue
            uid = _metadata_value(
                facts,
                component.get("uid"),
                source=filename,
                field="loader",
            )
            version = component.get("version")
            if uid == "net.minecraft":
                _add_minecraft_version(facts, version, filename)
            elif _loader_from_identifier(uid)[0] != UNKNOWN:
                _add_loader_identifier(facts, uid, filename, explicit_version=version)
        return

    if filename == "modrinth.index.json":
        dependencies = raw.get("dependencies")
        if not isinstance(dependencies, dict):
            raise ValueError("not a Modrinth index")
        _add_name(facts, raw.get("name"), filename, priority=20)
        _add_minecraft_version(facts, dependencies.get("minecraft"), filename)
        for key in ("forge", "neoforge", "fabric-loader", "quilt-loader"):
            if key in dependencies:
                _add_loader_identifier(
                    facts,
                    key,
                    filename,
                    explicit_version=dependencies.get(key),
                )
        return

    if filename == "profile.json":
        game_version = raw.get("game_version") or raw.get("gameVersion")
        loader = raw.get("loader") or raw.get("mod_loader")
        if game_version is None and loader is None:
            raise ValueError("not a recognized instance profile")
        _add_name(facts, raw.get("name"), filename, priority=30)
        _add_minecraft_version(facts, game_version, filename)
        _add_loader_identifier(
            facts,
            loader,
            filename,
            explicit_version=raw.get("loader_version") or raw.get("loaderVersion"),
        )
        return

    _parse_launcher_version_json(filename, raw, facts)


def _parse_launcher_version_json(
    filename: str,
    raw: dict[str, Any],
    facts: _MetadataFacts,
) -> None:
    game_version = raw.get("clientVersion") or raw.get("inheritsFrom")
    libraries = raw.get("libraries")
    loader_evidence: list[tuple[str, object]] = []
    if isinstance(libraries, list):
        for library in libraries:
            if not isinstance(library, dict):
                continue
            coordinate = library.get("name")
            if not isinstance(coordinate, str):
                continue
            parts = coordinate.split(":")
            if len(parts) < 2:
                continue
            group, artifact = parts[0].lower(), parts[1].lower()
            version = parts[2] if len(parts) >= 3 else None
            if group == "net.fabricmc" and artifact == "fabric-loader":
                loader_evidence.append(("fabric", version))
            elif group == "org.quiltmc" and artifact == "quilt-loader":
                loader_evidence.append(("quilt", version))
            elif group == "net.neoforged" and artifact in {
                "neoforge",
                "fancymodloader",
            }:
                loader_evidence.append(
                    ("neoforge", version if artifact == "neoforge" else None)
                )
            elif group == "net.minecraftforge" and artifact == "forge":
                loader_evidence.append(("forge", version))
    if game_version is None and not loader_evidence:
        raise ValueError("not a recognized launcher version manifest")
    _add_name(facts, raw.get("id"), filename, priority=50)
    _add_minecraft_version(facts, game_version, filename)
    for loader_name, loader_version in loader_evidence:
        _add_loader_identifier(
            facts,
            loader_name,
            filename,
            explicit_version=loader_version,
        )


def _parse_instance_cfg(text: str, filename: str, facts: _MetadataFacts) -> None:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string("[instance]\n" + text)
    values = parser["instance"]
    _add_name(facts, values.get("name"), filename, priority=40)
    _add_minecraft_version(
        facts,
        values.get("intendedversion") or values.get("minecraftversion"),
        filename,
    )
    for key, loader_name in (
        ("forgeversion", "forge"),
        ("neoforgeversion", "neoforge"),
        ("fabricloaderversion", "fabric"),
        ("quiltloaderversion", "quilt"),
    ):
        if values.get(key):
            _add_loader_identifier(
                facts,
                loader_name,
                filename,
                explicit_version=values.get(key),
            )


def _parse_pcl_setup(text: str, filename: str, facts: _MetadataFacts) -> None:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string("[instance]\n" + text)
    values = parser["instance"]
    _add_minecraft_version(
        facts,
        values.get("versionvanillaname"),
        filename,
    )
    for key, loader_name in (
        ("versionneoforge", "neoforge"),
        ("versionforge", "forge"),
        ("versionfabric", "fabric"),
        ("versionquilt", "quilt"),
    ):
        if values.get(key):
            _add_loader_identifier(
                facts,
                loader_name,
                filename,
                explicit_version=values.get(key),
            )


def _parse_pack_toml(text: str, filename: str, facts: _MetadataFacts) -> None:
    raw = tomllib.loads(text)
    versions = raw.get("versions")
    if not isinstance(versions, dict):
        raise ValueError("not a packwiz pack")
    _add_name(facts, raw.get("name"), filename, priority=20)
    _add_minecraft_version(facts, versions.get("minecraft"), filename)
    for key in ("forge", "neoforge", "fabric", "quilt"):
        if key in versions:
            _add_loader_identifier(
                facts,
                key,
                filename,
                explicit_version=versions.get(key),
            )


def _add_name(facts: _MetadataFacts, value: object, source: str, *, priority: int) -> None:
    cleaned = _metadata_value(facts, value, source=source, field="display_name")
    if cleaned:
        facts.names.append(_ValueEvidence(cleaned, source, priority))


def _add_minecraft_version(facts: _MetadataFacts, value: object, source: str) -> None:
    cleaned = _metadata_value(
        facts,
        value,
        source=source,
        field="minecraft_version",
    )
    if cleaned:
        facts.minecraft_versions.append(_ValueEvidence(cleaned, source, 0))


def _add_loader_identifier(
    facts: _MetadataFacts,
    value: object,
    source: str,
    *,
    explicit_version: object = None,
) -> None:
    identifier = _metadata_value(facts, value, source=source, field="loader")
    if not identifier:
        return
    loader_name, inferred_version = _loader_from_identifier(identifier)
    if loader_name == UNKNOWN:
        if identifier not in {"net.minecraft", "org.lwjgl", "com.mojang"}:
            facts.unknown_loader_ids.append((identifier, source))
        return
    version_value = explicit_version if explicit_version is not None else inferred_version
    if version_value and version_value != UNKNOWN:
        version = _metadata_value(
            facts,
            version_value,
            source=source,
            field="loader_version",
        )
        if not version:
            return
    else:
        version = UNKNOWN
    facts.loaders.append(_LoaderEvidence(loader_name, version, source))


def _loader_from_identifier(identifier: str) -> tuple[str, str]:
    normalized = identifier.strip().lower()
    mappings = (
        ("net.neoforged", "NeoForge"),
        ("neoforge", "NeoForge"),
        ("net.minecraftforge", "Forge"),
        ("forge", "Forge"),
        ("net.fabricmc.fabric-loader", "Fabric"),
        ("fabric-loader", "Fabric"),
        ("fabric", "Fabric"),
        ("org.quiltmc.quilt-loader", "Quilt"),
        ("quilt-loader", "Quilt"),
        ("quilt", "Quilt"),
    )
    for token, label in mappings:
        if normalized == token:
            return label, UNKNOWN
        if normalized.startswith(token + "-"):
            return label, identifier[len(token) + 1 :]
    return UNKNOWN, UNKNOWN


def _resolve_display_name(
    facts: _MetadataFacts,
    fallback: str,
    messages: list[InspectionMessage],
    evidence: list[str],
) -> str:
    if not facts.names:
        evidence.append("display name fallback: selected directory name")
        return fallback
    ordered = sorted(facts.names, key=lambda item: (item.priority, item.source, item.value))
    selected = ordered[0]
    distinct = {item.value for item in facts.names}
    evidence.extend(f"{item.source}: name={item.value}" for item in ordered)
    if len(distinct) > 1:
        messages.append(
            InspectionMessage(
                severity="warning",
                code="display_name_conflict",
                message="多个实例元数据提供了不同名称，已按元数据优先级显示其中一个。",
                location=".",
            )
        )
    return selected.value


def _resolve_minecraft_version(
    facts: _MetadataFacts,
    messages: list[InspectionMessage],
    evidence: list[str],
) -> str:
    values = {item.value for item in facts.minecraft_versions}
    evidence.extend(
        f"{item.source}: minecraft={item.value}"
        for item in sorted(facts.minecraft_versions, key=lambda item: (item.source, item.value))
    )
    if len(values) == 1:
        return next(iter(values))
    if len(values) > 1:
        messages.append(
            InspectionMessage(
                severity="warning",
                code="minecraft_version_conflict",
                message="实例元数据中的 Minecraft 版本互相冲突，已返回 unknown。",
                location=".",
            )
        )
    return UNKNOWN


def _resolve_loader(
    facts: _MetadataFacts,
    messages: list[InspectionMessage],
    evidence: list[str],
) -> LoaderInfo:
    evidence.extend(
        f"{item.source}: loader={item.name} {item.version}"
        for item in sorted(facts.loaders, key=lambda item: (item.source, item.name, item.version))
    )
    evidence.extend(
        f"{source}: unrecognized loader={identifier}"
        for identifier, source in sorted(facts.unknown_loader_ids)
    )
    names = {item.name for item in facts.loaders}
    versions = {item.version for item in facts.loaders if item.version != UNKNOWN}
    if len(names) > 1 or len(versions) > 1 or (names and facts.unknown_loader_ids):
        messages.append(
            InspectionMessage(
                severity="warning",
                code="loader_conflict",
                message="实例元数据中的 Loader 类型或版本互相冲突，已返回 unknown。",
                location=".",
            )
        )
        return LoaderInfo()
    if len(names) == 1:
        return LoaderInfo(
            name=next(iter(names)),
            version=next(iter(versions)) if versions else UNKNOWN,
        )
    if facts.unknown_loader_ids:
        messages.append(
            InspectionMessage(
                severity="warning",
                code="unsupported_loader_metadata",
                message="元数据包含无法识别的 Loader 标识，未进行猜测。",
                location=".",
            )
        )
    return LoaderInfo()


def _resolve_validity(
    *,
    facts: _MetadataFacts,
    markers: set[str],
    mod_count: int,
    valid_jar_count: int,
    wrong_level: bool,
) -> InspectionValidity:
    has_strong_metadata = bool(
        facts.files_parsed
        and (facts.minecraft_versions or facts.loaders)
    )
    if has_strong_metadata:
        return InspectionValidity.VALID
    has_established_instance_layout = (
        not wrong_level
        and "mods" in markers
        and bool(markers.intersection({"config", "resourcepacks", "kubejs"}))
        and mod_count >= MIN_VALID_JARS_FOR_INSTANCE
        and valid_jar_count >= MIN_VALID_JARS_FOR_INSTANCE
    )
    if has_established_instance_layout:
        return InspectionValidity.VALID
    if facts.files_found or markers or mod_count or wrong_level:
        return InspectionValidity.PROBABLE
    return InspectionValidity.INVALID


def _detect_instance_markers(
    root: Path,
    messages: list[InspectionMessage],
) -> set[str]:
    markers: set[str] = set()
    for name in INSTANCE_MARKERS:
        candidate = _safe_known_path(root, name, messages)
        if candidate is None:
            continue
        try:
            if candidate.is_dir():
                markers.add(name)
        except (OSError, RuntimeError) as error:
            _append_path_unreadable(messages, name, error)
    return markers


def _looks_like_selected_mods_directory(path: Path) -> bool:
    if path.name.lower() != "mods":
        return False
    try:
        contains_jars = any(item.is_file() and item.suffix.lower() == ".jar" for item in path.iterdir())
    except (OSError, RuntimeError):
        return False
    return contains_jars


def _discover_mod_jars(
    root: Path,
    messages: list[InspectionMessage],
) -> list[Path]:
    mods_dir = _safe_known_path(root, "mods", messages)
    if mods_dir is None:
        return []
    try:
        if not mods_dir.is_dir():
            return []
    except (OSError, RuntimeError) as error:
        _append_path_unreadable(messages, "mods", error)
        return []
    try:
        entries = sorted(
            mods_dir.iterdir(),
            key=lambda item: (item.name.casefold(), item.name),
        )
    except (OSError, RuntimeError) as error:
        _append_path_unreadable(messages, "mods", error)
        return []

    jars: list[Path] = []
    for entry in entries:
        if entry.suffix.lower() != ".jar":
            continue
        relative = f"mods/{entry.name}"
        try:
            safe_entry = resolve_path_for_operation(
                root,
                relative,
                label="mod JAR",
                allowed_top_levels={"mods"},
            )
        except UnsafePathError:
            messages.append(
                InspectionMessage(
                    severity="warning",
                    code="unsafe_mod_jar_path",
                    message="发现通过链接或重解析点指向其他位置的 JAR，已跳过。",
                    location=relative,
                )
            )
            continue
        except (OSError, RuntimeError) as error:
            _append_path_unreadable(messages, relative, error)
            continue
        try:
            if safe_entry.is_file():
                jars.append(safe_entry)
        except (OSError, RuntimeError) as error:
            _append_path_unreadable(messages, relative, error)
    return jars


def _safe_known_path(
    root: Path,
    relative: str,
    messages: list[InspectionMessage],
) -> Path | None:
    try:
        return resolve_path_for_operation(root, relative, label="instance path")
    except UnsafePathError:
        messages.append(
            InspectionMessage(
                severity="warning",
                code="unsafe_instance_path",
                message="实例中的路径经过符号链接或重解析点，已跳过。",
                location=relative,
            )
        )
        return None
    except (OSError, RuntimeError) as error:
        _append_path_unreadable(messages, relative, error)
        return None


def _append_path_unreadable(
    messages: list[InspectionMessage],
    relative: str,
    error: OSError | RuntimeError,
) -> None:
    messages.append(
        InspectionMessage(
            severity="warning",
            code="path_unreadable",
            message=f"无法读取实例路径：{type(error).__name__}。",
            location=relative,
        )
    )


def _glob_safe_files(
    root: Path,
    pattern: str,
    messages: list[InspectionMessage],
) -> set[str]:
    found: set[str] = set()
    pattern_location = _glob_pattern_location(pattern)
    try:
        candidates = root.glob(pattern)
        for candidate in candidates:
            relative = pattern_location
            try:
                relative = candidate.relative_to(root).as_posix()
                if not candidate.is_file():
                    continue
                safe_candidate = resolve_path_for_operation(root, relative, label="instance content")
            except (UnsafePathError, ValueError):
                continue
            except (OSError, RuntimeError) as error:
                _append_path_unreadable(messages, relative, error)
                continue
            try:
                if safe_candidate.is_file():
                    found.add(relative)
            except (OSError, RuntimeError) as error:
                _append_path_unreadable(messages, relative, error)
    except (OSError, RuntimeError) as error:
        _append_path_unreadable(messages, pattern_location, error)
        return found
    return found


def _glob_pattern_location(pattern: str) -> str:
    safe_parts: list[str] = []
    for part in PurePosixPath(pattern).parts:
        if any(character in part for character in "*?["):
            break
        safe_parts.append(part)
    return "/".join(safe_parts) or "."


def _message_from_zip_diagnostic(
    relative_jar: str,
    diagnostic: ZipDiagnostic,
) -> InspectionMessage:
    entry = ""
    if diagnostic.entry:
        try:
            entry = parse_untrusted_relative_path(diagnostic.entry, label="JAR entry").as_posix()
        except UnsafePathError:
            entry = "<不安全 entry>"
    location = relative_jar + (f" :: {entry}" if entry else "")
    if diagnostic.code == BAD_ZIP:
        message = "JAR 已损坏或不是有效的 ZIP，能力检测已跳过。"
    elif diagnostic.code == "read_error":
        message = "无法读取 JAR，能力检测已跳过。"
    else:
        message = f"JAR 安全限制已触发：{diagnostic.code}。"
    return InspectionMessage(
        severity="warning",
        code=f"jar_{diagnostic.code}",
        message=message,
        location=location,
    )


def _deduplicate_messages(messages: list[InspectionMessage]) -> list[InspectionMessage]:
    unique = {
        (message.severity, message.code, message.location, message.message): message
        for message in messages
    }
    severity_order = {"error": 0, "warning": 1, "info": 2}
    return sorted(
        unique.values(),
        key=lambda message: (
            severity_order.get(message.severity, 3),
            message.code,
            message.location.casefold(),
            message.location,
            message.message,
        ),
    )


def sanitize_metadata_text(value: object) -> str:
    """Return a safe display value without echoing rejected input in errors."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise TypeError("metadata value must be text or a finite number")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("metadata value must be finite")
    raw_text = str(value)
    if any(not character.isprintable() for character in raw_text):
        raise ValueError("metadata value contains control characters")
    text = raw_text.strip()
    if not text:
        raise ValueError("metadata value must not be empty")

    windows_path = PureWindowsPath(text)
    posix_path = PurePosixPath(text)
    if windows_path.drive or windows_path.root or windows_path.is_absolute():
        raise ValueError("metadata value resembles a Windows path")
    if posix_path.is_absolute():
        raise ValueError("metadata value resembles an absolute path")
    return text


def _metadata_value(
    facts: _MetadataFacts,
    value: object,
    *,
    source: str,
    field: str,
) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        return ""
    try:
        return sanitize_metadata_text(value)
    except (TypeError, ValueError):
        facts.rejected_values.append(_RejectedMetadataValue(source=source, field=field))
        return ""


def _append_rejected_metadata_messages(
    facts: _MetadataFacts,
    messages: list[InspectionMessage],
) -> None:
    for rejected in facts.rejected_values:
        messages.append(
            InspectionMessage(
                severity="warning",
                code="metadata_value_rejected",
                message=f"元数据字段 {rejected.field} 包含不安全或无效文本，已忽略。",
                location=rejected.source,
            )
        )


def _safe_fallback_name(value: str) -> str:
    try:
        return sanitize_metadata_text(value)
    except (TypeError, ValueError):
        return "Minecraft"


def _invalid_inspection(
    input_path: Path,
    *,
    display_name: str,
    started_at: float,
    code: str,
    message: str,
) -> ModpackInspection:
    return ModpackInspection(
        input_directory=input_path,
        validity=InspectionValidity.INVALID,
        display_name=display_name,
        minecraft_version=UNKNOWN,
        loader=LoaderInfo(),
        mod_count=0,
        capabilities=tuple(
            ContentCapability(key=key, label=CAPABILITY_LABELS[key], detected=False)
            for key in CAPABILITY_ORDER
        ),
        existing_chinese=ExistingChineseResources(
            status=ChineseResourceStatus.NONE,
        ),
        messages=(
            InspectionMessage(
                severity="error",
                code=code,
                message=message,
                location=".",
            ),
        ),
        evidence=(),
        inspection_duration=perf_counter() - started_at,
    )
