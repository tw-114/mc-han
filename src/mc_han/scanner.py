from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from time import perf_counter

from .extractors.ftbquests import (
    discover_en_us_lang_files,
    discover_ftbquest_snbt_files,
    scan_ftbquests,
)
from .extractors.jar import JarScanResult, inspect_and_scan_jar, scan_mod_jars
from .extractors.lang_sources import scan_filesystem_lang_sources
from .csv_store import write_extracted_csv
from .models import ExtractedText
from .utils.paths import relative_posix
from .utils.safe_zip import (
    ACTUAL_READ_LIMIT,
    BAD_ZIP,
    COMPRESSION_RATIO_LIMIT,
    ENCRYPTED_ENTRY,
    ENTRY_COUNT_LIMIT,
    ENTRY_SIZE_LIMIT,
    JAR_TOTAL_SIZE_LIMIT,
    READ_ERROR,
    DEFAULT_ZIP_LIMITS,
    ZipSafetyLimits,
)
from .workflow.provenance import (
    ExistingTranslationCandidate,
    SOURCE_PRIORITY,
    original_text_hash,
    record_mod_id,
)

MAX_ZIP_DIAGNOSTICS_IN_REPORT = 50


class ScanRecords(list[ExtractedText]):
    def __init__(
        self,
        records: list[ExtractedText],
        *,
        inventory: dict[str, object],
        provenance_candidates: tuple[ExistingTranslationCandidate, ...] = (),
    ) -> None:
        super().__init__(records)
        self.inventory = inventory
        self.provenance_candidates = tuple(provenance_candidates)


@dataclass(frozen=True)
class ScannerProgress:
    phase: str
    processed_jars: int
    total_jars: int
    current_source: str
    discovered_records: int


ScannerProgressCallback = Callable[[ScannerProgress], None]


def scan_modpack(
    modpack_dir: Path,
    *,
    translate_names: bool = False,
    zip_limits: ZipSafetyLimits = DEFAULT_ZIP_LIMITS,
    progress: ScannerProgressCallback | None = None,
) -> list[ExtractedText]:
    modpack_dir = Path(modpack_dir)
    records: list[ExtractedText] = []
    jar_results: list[tuple[str, JarScanResult]] = []
    phase_timings: dict[str, float] = {}
    jar_timings: list[tuple[str, float]] = []
    provenance_candidates: list[ExistingTranslationCandidate] = []
    mods_dir = modpack_dir / "mods"
    total_jars = len(list(mods_dir.glob("*.jar"))) if mods_dir.exists() else 0

    _notify_scan_progress(progress, "ftbquests", 0, total_jars, "", len(records))
    started_at = perf_counter()
    records.extend(
        scan_ftbquests(
            modpack_dir,
            provenance_candidates=provenance_candidates,
        )
    )
    phase_timings["ftbquests"] = perf_counter() - started_at

    _notify_scan_progress(progress, "filesystem", 0, total_jars, "", len(records))
    started_at = perf_counter()
    records.extend(
        scan_filesystem_lang_sources(
            modpack_dir,
            translate_names=translate_names,
            provenance_candidates=provenance_candidates,
        )
    )
    phase_timings["filesystem"] = perf_counter() - started_at

    started_at = perf_counter()
    records.extend(
        scan_mod_jars(
            modpack_dir,
            translate_names=translate_names,
            limits=zip_limits,
            jar_results=jar_results,
            progress=(
                lambda processed, total, source, discovered: _notify_scan_progress(
                    progress,
                    "jars",
                    processed,
                    total,
                    source,
                    discovered,
                )
            ),
            initial_record_count=len(records),
            timings=jar_timings,
        )
    )
    phase_timings["jars"] = perf_counter() - started_at
    for _container, result in jar_results:
        provenance_candidates.extend(result.provenance_candidates)

    _notify_scan_progress(
        progress,
        "sorting",
        total_jars,
        total_jars,
        "",
        len(records),
    )
    started_at = perf_counter()
    sorted_records = sorted(
        apply_existing_translation_candidates(records, provenance_candidates),
        key=lambda item: (
            item.source_type,
            item.container,
            item.file_path,
            item.key_path,
            item.original,
        ),
    )
    phase_timings["sorting"] = perf_counter() - started_at

    started_at = perf_counter()
    inventory = build_scan_inventory(modpack_dir, jar_results)
    phase_timings["inventory"] = perf_counter() - started_at
    inventory["scan_phase_seconds"] = phase_timings
    inventory["jar_scan_seconds"] = tuple(jar_timings)
    inventory["existing_chinese_reused_records"] = sum(
        bool(record.translation.strip()) for record in sorted_records
    )
    return ScanRecords(
        sorted_records,
        inventory=inventory,
        provenance_candidates=tuple(
            sorted(
                provenance_candidates,
                key=lambda item: (
                    item.record_id,
                    -SOURCE_PRIORITY[item.source],
                    item.source_location.casefold(),
                    item.source_location,
                ),
            )
        ),
    )


def _notify_scan_progress(
    callback: ScannerProgressCallback | None,
    phase: str,
    processed_jars: int,
    total_jars: int,
    current_source: str,
    discovered_records: int,
) -> None:
    if callback is not None:
        callback(
            ScannerProgress(
                phase=phase,
                processed_jars=processed_jars,
                total_jars=total_jars,
                current_source=current_source,
                discovered_records=discovered_records,
            )
        )


def merge_existing_translations(
    records: list[ExtractedText],
    existing_records: list[ExtractedText],
) -> list[ExtractedText]:
    """Reuse user work only when source, context, and original text are unchanged.

    Legacy review/skip markers stored in ``note`` remain supported alongside the
    structured review_status and skip_status CSV columns.
    """
    existing_by_key = {
        translation_reuse_key(record): record
        for record in existing_records
        if (
            record.translation.strip()
            or record.note.strip()
            or record.review_status.strip()
            or record.skip_status.strip()
        )
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
                review_status=existing.review_status,
                skip_status=existing.skip_status,
            )
        )
    if isinstance(records, ScanRecords):
        return ScanRecords(
            merged,
            inventory=records.inventory,
            provenance_candidates=records.provenance_candidates,
        )
    return merged


def apply_existing_translation_candidates(
    records: list[ExtractedText],
    candidates: list[ExistingTranslationCandidate]
    | tuple[ExistingTranslationCandidate, ...],
) -> list[ExtractedText]:
    by_record: dict[str, list[ExistingTranslationCandidate]] = {}
    for candidate in candidates:
        by_record.setdefault(candidate.record_id, []).append(candidate)

    updated: list[ExtractedText] = []
    for record in records:
        matching = [
            candidate
            for candidate in by_record.get(record.id, ())
            if candidate.key_path == record.key_path
            and candidate.mod_id == record_mod_id(record)
            and candidate.original_hash == original_text_hash(record.original)
        ]
        if not matching:
            updated.append(record)
            continue
        selected = max(
            matching,
            key=lambda item: (
                SOURCE_PRIORITY[item.source],
                item.source_location.casefold(),
                item.source_location,
            ),
        )
        updated.append(
            replace(
                record,
                translation=selected.translation,
                note=f"source:{selected.source.value}",
            )
        )
    return updated


def translation_reuse_key(record: ExtractedText) -> tuple[str, str, str, str, str]:
    return (record.source_type, record.container, record.file_path, record.key_path, record.original)


def write_scan_report(
    *,
    modpack_dir: Path,
    records: list[ExtractedText],
    output_csv: Path,
    report_path: Path,
    elapsed_seconds: float | None = None,
    zip_limits: ZipSafetyLimits = DEFAULT_ZIP_LIMITS,
) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        build_scan_report(
            modpack_dir=modpack_dir,
            records=records,
            output_csv=output_csv,
            elapsed_seconds=elapsed_seconds,
            zip_limits=zip_limits,
        ),
        encoding="utf-8",
    )


def build_scan_report(
    *,
    modpack_dir: Path,
    records: list[ExtractedText],
    output_csv: Path,
    elapsed_seconds: float | None = None,
    zip_limits: ZipSafetyLimits = DEFAULT_ZIP_LIMITS,
) -> str:
    modpack_dir = Path(modpack_dir)
    source_counts = Counter(record.source_type for record in records)
    container_counts = Counter(record.container for record in records)
    inventory = (
        records.inventory
        if isinstance(records, ScanRecords)
        else inspect_scan_inputs(modpack_dir, zip_limits=zip_limits)
    )
    output_display = report_relative_path(output_csv, modpack_dir)

    lines = [
        "mc-han scan report",
        f"generated_at: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        "modpack_dir: .",
        f"output_csv: {output_display}",
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
        f"  jars_stopped_by_entry_count_limit: {inventory['jars_stopped_by_entry_count_limit']}",
        f"  entries_rejected_by_size_limit: {inventory['entries_rejected_by_size_limit']}",
        f"  jars_stopped_by_candidate_total_limit: {inventory['jars_stopped_by_candidate_total_limit']}",
        f"  entries_rejected_by_compression_ratio: {inventory['entries_rejected_by_compression_ratio']}",
        f"  entries_rejected_by_actual_read_limit: {inventory['entries_rejected_by_actual_read_limit']}",
        f"  encrypted_entries_rejected: {inventory['encrypted_entries_rejected']}",
        f"  jar_read_errors: {inventory['jar_read_errors']}",
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

    lines.extend(["", "jar_safety_diagnostics"])
    if inventory["jar_safety_diagnostics"]:
        for container, diagnostic in inventory["jar_safety_diagnostics"][
            :MAX_ZIP_DIAGNOSTICS_IN_REPORT
        ]:
            entry = f" :: {diagnostic.entry}" if diagnostic.entry else ""
            lines.append(f"  [{diagnostic.code}] {container}{entry} - {diagnostic.reason}")
        omitted = len(inventory["jar_safety_diagnostics"]) - MAX_ZIP_DIAGNOSTICS_IN_REPORT
        if omitted > 0:
            lines.append(f"  ... {omitted} additional diagnostic(s) omitted")
    else:
        lines.append("  none")

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


def inspect_scan_inputs(
    modpack_dir: Path,
    *,
    zip_limits: ZipSafetyLimits = DEFAULT_ZIP_LIMITS,
) -> dict[str, object]:
    modpack_dir = Path(modpack_dir)
    mods_dir = modpack_dir / "mods"
    jar_paths = sorted(mods_dir.glob("*.jar")) if mods_dir.exists() else []
    jar_results: list[tuple[str, JarScanResult]] = []

    for jar_path in jar_paths:
        container = relative_posix(jar_path, modpack_dir)
        jar_results.append(
            (
                container,
                inspect_and_scan_jar(
                    jar_path,
                    container=container,
                    limits=zip_limits,
                    read_contents=False,
                ),
            )
        )
    return build_scan_inventory(modpack_dir, jar_results)


def build_scan_inventory(
    modpack_dir: Path,
    jar_results: list[tuple[str, JarScanResult]],
) -> dict[str, object]:
    supported_jar_entries_found: Counter[str] = Counter()
    bad_jar_files: list[str] = []
    jar_lang_en_us_files_found = 0
    jar_safety_diagnostics = []

    for container, result in jar_results:
        supported_jar_entries_found.update(result.supported_entries)
        jar_lang_en_us_files_found += result.supported_entries["jar_lang"]
        for diagnostic in result.diagnostics:
            jar_safety_diagnostics.append((container, diagnostic))
            if diagnostic.code == BAD_ZIP:
                bad_jar_files.append(container)

    diagnostic_counts = Counter(
        diagnostic.code for _container, diagnostic in jar_safety_diagnostics
    )
    jars_by_code = {
        code: {
            container
            for container, diagnostic in jar_safety_diagnostics
            if diagnostic.code == code
        }
        for code in (ENTRY_COUNT_LIMIT, JAR_TOTAL_SIZE_LIMIT)
    }

    lang_root = modpack_dir / "config" / "ftbquests" / "quests" / "lang"
    ftb_lang_files = discover_en_us_lang_files(lang_root) if lang_root.exists() else []

    return {
        "jar_files_found": len(jar_results),
        "bad_jar_files": bad_jar_files,
        "supported_jar_entries_found": supported_jar_entries_found,
        "jar_lang_en_us_files_found": jar_lang_en_us_files_found,
        "jars_stopped_by_entry_count_limit": len(jars_by_code[ENTRY_COUNT_LIMIT]),
        "entries_rejected_by_size_limit": diagnostic_counts[ENTRY_SIZE_LIMIT],
        "jars_stopped_by_candidate_total_limit": len(
            jars_by_code[JAR_TOTAL_SIZE_LIMIT]
        ),
        "entries_rejected_by_compression_ratio": diagnostic_counts[
            COMPRESSION_RATIO_LIMIT
        ],
        "entries_rejected_by_actual_read_limit": diagnostic_counts[
            ACTUAL_READ_LIMIT
        ],
        "encrypted_entries_rejected": diagnostic_counts[ENCRYPTED_ENTRY],
        "jar_read_errors": diagnostic_counts[READ_ERROR],
        "jar_safety_diagnostics": jar_safety_diagnostics,
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


def report_relative_path(path: Path, modpack_dir: Path) -> str:
    try:
        return Path(path).resolve(strict=False).relative_to(
            Path(modpack_dir).resolve(strict=False)
        ).as_posix()
    except ValueError:
        return Path(path).name
