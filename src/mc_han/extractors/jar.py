from __future__ import annotations

import json
import zipfile
from pathlib import Path, PurePosixPath

from mc_han.extractors.guideme import extract_markdown
from mc_han.extractors.lang_json import extract_flat_lang_json
from mc_han.extractors.modonomicon import extract_modonomicon_json
from mc_han.extractors.patchouli import extract_patchouli_json
from mc_han.models import ExtractedText
from mc_han.utils.encoding import decode_text
from mc_han.utils.paths import relative_posix


def scan_mod_jars(modpack_dir: Path, *, translate_names: bool = False) -> list[ExtractedText]:
    mods_dir = modpack_dir / "mods"
    if not mods_dir.exists():
        return []

    records: list[ExtractedText] = []
    for jar_path in sorted(mods_dir.glob("*.jar")):
        records.extend(scan_jar(jar_path, container=relative_posix(jar_path, modpack_dir), translate_names=translate_names))
    return records


def scan_jar(jar_path: Path, *, container: str, translate_names: bool = False) -> list[ExtractedText]:
    records: list[ExtractedText] = []
    try:
        with zipfile.ZipFile(jar_path) as jar:
            for name in sorted(jar.namelist()):
                if name.endswith("/"):
                    continue
                source_type = classify_jar_entry(name)
                if source_type is None:
                    continue
                content = decode_text(jar.read(name))
                try:
                    records.extend(
                        extract_jar_entry(
                            content,
                            source_type,
                            container,
                            name,
                            translate_names=translate_names,
                        )
                    )
                except json.JSONDecodeError:
                    continue
    except zipfile.BadZipFile:
        return []
    return records


def classify_jar_entry(name: str) -> str | None:
    path = PurePosixPath(name)
    parts = path.parts
    lower_parts = tuple(part.lower() for part in parts)
    lower_name = name.lower()

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
