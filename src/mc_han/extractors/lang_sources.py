from __future__ import annotations

from pathlib import Path

from mc_han.extractors.lang_json import extract_flat_lang_json
from mc_han.models import ExtractedText
from mc_han.utils.encoding import decode_text
from mc_han.utils.paths import relative_posix


def scan_filesystem_lang_sources(modpack_dir: Path, *, translate_names: bool = False) -> list[ExtractedText]:
    records: list[ExtractedText] = []
    records.extend(scan_kubejs_lang(modpack_dir, translate_names=translate_names))
    records.extend(scan_resourcepack_lang(modpack_dir, translate_names=translate_names))
    return records


def scan_kubejs_lang(modpack_dir: Path, *, translate_names: bool = False) -> list[ExtractedText]:
    root = modpack_dir / "kubejs"
    if not root.exists():
        return []
    return scan_lang_files(
        modpack_dir=modpack_dir,
        paths=sorted(root.glob("assets/**/lang/en_us.json")),
        source_type="kubejs_lang",
        translate_names=translate_names,
    )


def scan_resourcepack_lang(modpack_dir: Path, *, translate_names: bool = False) -> list[ExtractedText]:
    root = modpack_dir / "resourcepacks"
    if not root.exists():
        return []
    return scan_lang_files(
        modpack_dir=modpack_dir,
        paths=sorted(root.glob("**/assets/**/lang/en_us.json")),
        source_type="resourcepack_lang",
        translate_names=translate_names,
    )


def scan_lang_files(
    *,
    modpack_dir: Path,
    paths: list[Path],
    source_type: str,
    translate_names: bool = False,
) -> list[ExtractedText]:
    records: list[ExtractedText] = []
    for path in paths:
        rel_path = relative_posix(path, modpack_dir)
        try:
            content = decode_text(path.read_bytes())
            records.extend(
                extract_flat_lang_json(
                    content,
                    source_type=source_type,
                    container="modpack",
                    file_path=rel_path,
                    translate_names=translate_names,
                    allow_name_keys=True,
                )
            )
        except ValueError:
            continue
    return records
