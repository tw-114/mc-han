from __future__ import annotations

import json
from pathlib import Path

from mc_han.extractors.lang_json import extract_flat_lang_json
from mc_han.models import ExtractedText
from mc_han.utils.encoding import decode_text
from mc_han.utils.paths import relative_posix
from mc_han.workflow.provenance import (
    ExistingTranslationCandidate,
    TranslationSource,
    original_text_hash,
    record_artifact_version,
    record_mod_id,
)


def scan_filesystem_lang_sources(
    modpack_dir: Path,
    *,
    translate_names: bool = False,
    provenance_candidates: list[ExistingTranslationCandidate] | None = None,
) -> list[ExtractedText]:
    records: list[ExtractedText] = []
    records.extend(
        scan_kubejs_lang(
            modpack_dir,
            translate_names=translate_names,
            provenance_candidates=provenance_candidates,
        )
    )
    records.extend(
        scan_resourcepack_lang(
            modpack_dir,
            translate_names=translate_names,
            provenance_candidates=provenance_candidates,
        )
    )
    return records


def scan_kubejs_lang(
    modpack_dir: Path,
    *,
    translate_names: bool = False,
    provenance_candidates: list[ExistingTranslationCandidate] | None = None,
) -> list[ExtractedText]:
    root = modpack_dir / "kubejs"
    if not root.exists():
        return []
    return scan_lang_files(
        modpack_dir=modpack_dir,
        paths=sorted(root.glob("assets/**/lang/en_us.json")),
        source_type="kubejs_lang",
        translate_names=translate_names,
        existing_source=TranslationSource.MODPACK_AUTHOR,
        provenance_candidates=provenance_candidates,
    )


def scan_resourcepack_lang(
    modpack_dir: Path,
    *,
    translate_names: bool = False,
    provenance_candidates: list[ExistingTranslationCandidate] | None = None,
) -> list[ExtractedText]:
    root = modpack_dir / "resourcepacks"
    if not root.exists():
        return []
    return scan_lang_files(
        modpack_dir=modpack_dir,
        paths=sorted(root.glob("**/assets/**/lang/en_us.json")),
        source_type="resourcepack_lang",
        translate_names=translate_names,
        existing_source=TranslationSource.MODPACK_AUTHOR,
        provenance_candidates=provenance_candidates,
    )


def scan_lang_files(
    *,
    modpack_dir: Path,
    paths: list[Path],
    source_type: str,
    translate_names: bool = False,
    existing_source: TranslationSource | None = None,
    provenance_candidates: list[ExistingTranslationCandidate] | None = None,
) -> list[ExtractedText]:
    records: list[ExtractedText] = []
    for path in paths:
        rel_path = relative_posix(path, modpack_dir)
        try:
            content = decode_text(path.read_bytes())
            extracted = extract_flat_lang_json(
                content,
                source_type=source_type,
                container="modpack",
                file_path=rel_path,
                translate_names=translate_names,
                allow_name_keys=True,
            )
            records.extend(extracted)
            if existing_source is not None and provenance_candidates is not None:
                provenance_candidates.extend(
                    _paired_zh_cn_candidates(
                        path,
                        modpack_dir=modpack_dir,
                        records=extracted,
                        source=existing_source,
                    )
                )
        except (OSError, ValueError):
            continue
    return records


def _paired_zh_cn_candidates(
    en_path: Path,
    *,
    modpack_dir: Path,
    records: list[ExtractedText],
    source: TranslationSource,
) -> tuple[ExistingTranslationCandidate, ...]:
    zh_path = en_path.with_name("zh_cn.json")
    if not zh_path.is_file():
        return ()
    try:
        raw = json.loads(decode_text(zh_path.read_bytes()))
    except (OSError, UnicodeError, ValueError):
        return ()
    if not isinstance(raw, dict):
        return ()
    source_location = relative_posix(zh_path, modpack_dir)
    candidates: list[ExistingTranslationCandidate] = []
    for record in records:
        translation = raw.get(record.key_path)
        if not isinstance(translation, str) or not translation.strip():
            continue
        candidates.append(
            ExistingTranslationCandidate(
                record_id=record.id,
                source=source,
                translation=translation,
                mod_id=record_mod_id(record),
                key_path=record.key_path,
                original_hash=original_text_hash(record.original),
                artifact_version=record_artifact_version(record),
                source_location=source_location,
            )
        )
    return tuple(candidates)
