from __future__ import annotations

import json
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from mc_han.extractors.guideme import extract_markdown
from mc_han.extractors.lang_json import extract_flat_lang_json
from mc_han.extractors.modonomicon import extract_modonomicon_json
from mc_han.extractors.patchouli import extract_patchouli_json
from mc_han.models import ExtractedText
from mc_han.utils.encoding import decode_text
from mc_han.utils.paths import relative_posix
from mc_han.utils.safe_paths import UnsafePathError, parse_untrusted_relative_path
from mc_han.utils.safe_zip import (
    DEFAULT_ZIP_LIMITS,
    READ_ERROR,
    SafeZipReader,
    ZipDiagnostic,
    ZipSafetyError,
    ZipSafetyLimits,
    bad_zip_diagnostic,
)


@dataclass(frozen=True)
class JarScanResult:
    records: list[ExtractedText]
    supported_entries: Counter[str]
    diagnostics: list[ZipDiagnostic]


def scan_mod_jars(
    modpack_dir: Path,
    *,
    translate_names: bool = False,
    limits: ZipSafetyLimits = DEFAULT_ZIP_LIMITS,
    diagnostics: list[tuple[str, ZipDiagnostic]] | None = None,
    jar_results: list[tuple[str, JarScanResult]] | None = None,
) -> list[ExtractedText]:
    mods_dir = modpack_dir / "mods"
    if not mods_dir.exists():
        return []

    records: list[ExtractedText] = []
    for jar_path in sorted(mods_dir.glob("*.jar")):
        container = relative_posix(jar_path, modpack_dir)
        result = inspect_and_scan_jar(
            jar_path,
            container=container,
            translate_names=translate_names,
            limits=limits,
        )
        records.extend(result.records)
        if diagnostics is not None:
            diagnostics.extend((container, item) for item in result.diagnostics)
        if jar_results is not None:
            jar_results.append((container, result))
    return records


def scan_jar(
    jar_path: Path,
    *,
    container: str,
    translate_names: bool = False,
    limits: ZipSafetyLimits = DEFAULT_ZIP_LIMITS,
    diagnostics: list[tuple[str, ZipDiagnostic]] | None = None,
) -> list[ExtractedText]:
    result = inspect_and_scan_jar(
        jar_path,
        container=container,
        translate_names=translate_names,
        limits=limits,
    )
    if diagnostics is not None:
        diagnostics.extend((container, item) for item in result.diagnostics)
    return result.records


def inspect_and_scan_jar(
    jar_path: Path,
    *,
    container: str,
    translate_names: bool = False,
    limits: ZipSafetyLimits = DEFAULT_ZIP_LIMITS,
    read_contents: bool = True,
) -> JarScanResult:
    records: list[ExtractedText] = []
    supported_entries: Counter[str] = Counter()
    diagnostics: list[ZipDiagnostic] = []
    try:
        with zipfile.ZipFile(jar_path) as jar:
            reader: SafeZipReader[str] = SafeZipReader(jar, limits=limits)

            def select_candidate(name: str) -> str | None:
                source_type = classify_jar_entry(name)
                if source_type is not None:
                    supported_entries[source_type] += 1
                return source_type

            for info, source_type in reader.iter_candidates(select_candidate):
                if not read_contents:
                    continue
                try:
                    content = decode_text(reader.read_entry(info))
                except ZipSafetyError as error:
                    if error.diagnostic.stops_jar:
                        break
                    continue
                if source_type is None:
                    continue
                try:
                    records.extend(
                        extract_jar_entry(
                            content,
                            source_type,
                            container,
                            info.filename,
                            translate_names=translate_names,
                        )
                    )
                except json.JSONDecodeError:
                    continue
            diagnostics.extend(reader.diagnostics)
    except (zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        diagnostics.append(bad_zip_diagnostic(error))
    except OSError as error:
        diagnostics.append(
            ZipDiagnostic(
                code=READ_ERROR,
                entry=None,
                reason=f"cannot read ZIP archive: {type(error).__name__}",
                stops_jar=True,
            )
        )
    return JarScanResult(
        records=records,
        supported_entries=supported_entries,
        diagnostics=diagnostics,
    )


def classify_jar_entry(name: str) -> str | None:
    try:
        safe_name = parse_untrusted_relative_path(name, label="JAR entry")
    except UnsafePathError:
        return None
    path = PurePosixPath(safe_name.as_posix())
    parts = path.parts
    lower_parts = tuple(part.lower() for part in parts)
    lower_name = path.as_posix().lower()

    if not lower_parts or lower_parts[0] != "assets":
        return None

    if lower_name.endswith("/lang/en_us.json"):
        return "jar_lang"
    if lower_name.endswith(".md") and "ae2guide" in lower_parts:
        return "jar_ae2guide"
    if lower_name.endswith(".md") and "guides" in lower_parts:
        return "jar_guides"
    if (
        lower_name.endswith(".json")
        and "patchouli_books" in lower_parts
        and "en_us" in lower_parts
    ):
        return "jar_patchouli"
    if (
        lower_name.endswith(".json")
        and "modonomicon" in lower_parts
        and "books" in lower_parts
        and "en_us" in lower_parts
    ):
        return "jar_modonomicon"
    return None


def extract_jar_entry(
    content: str,
    source_type: str,
    container: str,
    file_path: str,
    *,
    translate_names: bool = False,
) -> list[ExtractedText]:
    if source_type in {"jar_ae2guide", "jar_guides"}:
        return extract_markdown(
            content,
            source_type=source_type,
            container=container,
            file_path=file_path,
        )
    if source_type == "jar_lang":
        return extract_flat_lang_json(
            content,
            source_type=source_type,
            container=container,
            file_path=file_path,
            translate_names=translate_names,
            allow_name_keys=True,
        )
    if source_type == "jar_patchouli":
        return extract_patchouli_json(
            content,
            source_type=source_type,
            container=container,
            file_path=file_path,
        )
    if source_type == "jar_modonomicon":
        return extract_modonomicon_json(
            content,
            source_type=source_type,
            container=container,
            file_path=file_path,
        )
    return []
