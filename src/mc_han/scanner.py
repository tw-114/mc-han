from __future__ import annotations

import zipfile
from collections import Counter
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from .extractors.ftbquests import (
    discover_en_us_lang_files,
    discover_ftbquest_snbt_files,
    scan_ftbquests,
)
from .extractors.jar import classify_jar_entry, scan_mod_jars
from .extractors.lang_sources import scan_filesystem_lang_sources
from .csv_store import write_extracted_csv
from .models import ExtractedText
from .utils.paths import relative_posix


def scan_modpack(modpack_dir: Path, *, translate_names: bool = False) -> list[ExtractedText]:
    modpack_dir = Path(modpack_dir)
    records: list[ExtractedText] = []
    records.extend(scan_ftbquests(modpack_dir))
    records.extend(scan_filesystem_lang_sources(modpack_dir, translate_names=translate_names))
    records.extend(scan_mod_jars(modpack_dir, translate_names=translate_names))
    return sorted(
        records,
        key=lambda item: (
            item.source_type,
            item.container,
            item.file_path,
            item.key_path,
            item.original,
        ),
    )


def merge_existing_translations(
    records: list[ExtractedText],
    existing_records: list[ExtractedText],
) -> list[ExtractedText]:
    existing_by_key = {
        translation_reuse_key(record): record
        for record in existing_records
        if record.translation.strip() or record.note.strip()
    }
    merged: list[ExtractedText] = []
    for record in records:
        existing = existing_by_key.get(translation_reuse_key(record))
        if existing is None:
            merged.append(record)
            continue
        merged.append(
            replace(
                record,
                translation=existing.translation,
                note=existing.note,
            )
        )
    return merged


def translation_reuse_key(record: ExtractedText) -> tuple[str, str, str, str, str]:
    return (record.source_type, record.container, record.file_path, record.key_path, record.original)


def write_scan_report(
    *,
    modpack_dir: Path,
    records: list[ExtractedText],
    output_csv: Path,
    report_path: Path,
    elapsed_seconds: float | None = None,
) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        build_scan_report(
            modpack_dir=modpack_dir,
            records=records,
            output_csv=output_csv,
            elapsed_seconds=elapsed_seconds,
        ),
        encoding="utf-8",
    )


def build_scan_report(
    *,
    modpack_dir: Path,
    records: list[ExtractedText],
    output_csv: Path,
    elapsed_seconds: float | None = None,
) -> str:
    modpack_dir = Path(modpack_dir)
    source_counts = Counter(record.source_type for record in records)
    container_counts = Counter(record.container for record in records)
    inventory = inspect_scan_inputs(modpack_dir)

    lines = [
        "mc-han scan report",
        f"generated_at: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"modpack_dir: {modpack_dir}",
        f"output_csv: {output_csv}",
        f"elapsed_seconds: {elapsed_seconds:.3f}" if elapsed_seconds is not None else "elapsed_seconds: unknown",
        "",
        "summary",
        f"  extracted_rows: {len(records)}",
        f"  unique_containers_with_rows: {len(container_counts)}",
        f"  jar_files_found: {inventory['jar_files_found']}",
        f"  bad_jar_files: {len(inventory['bad_jar_files'])}",
        f"  ftb_lang_files_found: {inventory['ftb_lang_files_found']}",
        f"  ftb_snbt_files_found: {inventory['ftb_snbt_files_found']}",
        f"  kubejs_lang_en_us_files_found: {inventory['kubejs_lang_en_us_files_found']}",
        f"  resourcepack_lang_en_us_files_found: {inventory['resourcepack_lang_en_us_files_found']}",
        f"  resourcepack_lang_zh_cn_files_found: {inventory['resourcepack_lang_zh_cn_files_found']}",
        f"  jar_lang_en_us_files_found: {inventory['jar_lang_en_us_files_found']}",
        "",
        "extracted_rows_by_source",
    ]

    if source_counts:
        for source_type, count in source_counts.most_common():
            lines.append(f"  {source_type}: {count}")
    else:
        lines.append("  none")

    lines.extend(["", "supported_jar_entries_found"])
    if inventory["supported_jar_entries_found"]:
        for source_type, count in inventory["supported_jar_entries_found"].most_common():
            lines.append(f"  {source_type}: {count}")
    else:
        lines.append("  none")

    lines.extend(["", "top_containers_by_extracted_rows"])
    if container_counts:
        for container, count in container_counts.most_common(20):
            lines.append(f"  {container}: {count}")
    else:
        lines.append("  none")

    if inventory["bad_jar_files"]:
        lines.extend(["", "bad_jar_files"])
        for jar in inventory["bad_jar_files"][:50]:
            lines.append(f"  {jar}")

    lines.extend(
        [
            "",
            "notes",
            "  This scan is read-only and never modifies mods/*.jar.",
            "  First-phase translation rows leave the translation column empty.",
            "  Base-name language keys such as item.*, block.*, entity.*, and fluid.* are skipped unless name translation is enabled.",
        ]
    )
    return "\n".join(lines) + "\n"


def inspect_scan_inputs(modpack_dir: Path) -> dict[str, object]:
    modpack_dir = Path(modpack_dir)
    mods_dir = modpack_dir / "mods"
    jar_paths = sorted(mods_dir.glob("*.jar")) if mods_dir.exists() else []
    supported_jar_entries_found: Counter[str] = Counter()
    bad_jar_files: list[str] = []
    jar_lang_en_us_files_found = 0

    for jar_path in jar_paths:
        try:
            with zipfile.ZipFile(jar_path) as jar:
                for name in jar.namelist():
                    if name.endswith("/"):
                        continue
                    source_type = classify_jar_entry(name)
                    if source_type is not None:
                        supported_jar_entries_found[source_type] += 1
                    if is_jar_lang_en_us_entry(name):
                        jar_lang_en_us_files_found += 1
        except zipfile.BadZipFile:
            bad_jar_files.append(relative_posix(jar_path, modpack_dir))

    lang_root = modpack_dir / "config" / "ftbquests" / "quests" / "lang"
    ftb_lang_files = discover_en_us_lang_files(lang_root) if lang_root.exists() else []

    return {
        "jar_files_found": len(jar_paths),
        "bad_jar_files": bad_jar_files,
        "supported_jar_entries_found": supported_jar_entries_found,
        "jar_lang_en_us_files_found": jar_lang_en_us_files_found,
        "ftb_lang_files_found": len(ftb_lang_files),
        "ftb_snbt_files_found": len(discover_ftbquest_snbt_files(modpack_dir)),
        "kubejs_lang_en_us_files_found": len(list((modpack_dir / "kubejs").glob("assets/**/lang/en_us.json"))),
        "resourcepack_lang_en_us_files_found": len(
            list((modpack_dir / "resourcepacks").glob("**/assets/**/lang/en_us.json"))
        ),
        "resourcepack_lang_zh_cn_files_found": len(
            list((modpack_dir / "resourcepacks").glob("**/assets/**/lang/zh_cn.json"))
        ),
    }


def is_jar_lang_en_us_entry(name: str) -> bool:
    normalized = name.lower().replace("\\", "/")
    return normalized.startswith("assets/") and normalized.endswith("/lang/en_us.json")
