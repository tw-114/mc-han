from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from time import perf_counter
from typing import Callable, Sequence

from .config import DEFAULT_NAME_TRANSLATION_FORMAT
from .builder.installer import install_outputs, plan_install_outputs, rollback_install, write_install_plan_report
from .builder.resourcepack import build_outputs, default_build_dir
from .checkpoints import create_checkpoint, csv_status, list_checkpoints, rollback_checkpoint
from .csv_store import read_extracted_csv
from .preview import build_preview
from .quality.checks import check_csv, check_output_dir, write_quality_report
from .review import write_review_report
from .version import get_version
from .scanner import merge_existing_translations, scan_modpack, write_extracted_csv, write_scan_report
from .services.modpack_inspector import inspect_modpack
from .settings import UserSettings, clear_settings, config_path, load_settings, masked_api_key, merge_settings, save_settings
from .translator.engine import TranslationProgress, translate_csv
from .translator.mock_provider import MockTranslator
from .translator.openai_provider import OpenAICompatibleTranslator, PROVIDER_PRESETS
from .workflow.models import ChineseResourceStatus, InspectionValidity, ModpackInspection

CUSTOM_PROVIDER = "custom"
PROVIDER_CHOICES = ["mock", CUSTOM_PROVIDER, *sorted(PROVIDER_PRESETS)]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mc-han",
        description="Minecraft modpack localization assistant.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {get_version()}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("gui", help="Open the desktop GUI.")

    inspect_parser = subparsers.add_parser(
        "inspect",
        help="Inspect a modpack directory without scanning text content.",
    )
    inspect_parser.add_argument("modpack_dir", type=Path, help="Minecraft modpack directory.")
    inspect_parser.add_argument("--json", action="store_true", dest="json_output", help="Output machine-readable JSON.")

    config_parser = subparsers.add_parser("config", help="Show, save, or clear local provider settings.")
    config_subparsers = config_parser.add_subparsers(dest="config_command", required=True)
    config_subparsers.add_parser("show", help="Show saved local settings with the API key masked.")
    config_save_parser = config_subparsers.add_parser("save", help="Save local provider settings.")
    config_save_parser.add_argument("--provider", choices=PROVIDER_CHOICES, default=None)
    config_save_parser.add_argument("--model", default=None)
    config_save_parser.add_argument("--api-key", default=None)
    config_save_parser.add_argument("--api-key-env", default=None)
    config_save_parser.add_argument("--base-url", default=None)
    config_save_parser.add_argument("--limit", type=int, default=None)
    config_save_parser.add_argument("--speed-mode", choices=("safe", "balanced", "fast"), default=None)
    config_save_parser.add_argument("--concurrency", type=int, choices=(1, 2, 3), default=None)
    config_save_parser.add_argument("--max-batch-items", type=int, default=None)
    config_save_parser.add_argument("--max-input-tokens", type=int, default=None)
    config_save_parser.add_argument("--max-output-tokens", type=int, default=None)
    config_save_parser.set_defaults(translate_names=None)
    config_save_parser.add_argument("--translate-names", action="store_true", dest="translate_names")
    config_save_parser.add_argument("--no-translate-names", action="store_false", dest="translate_names")
    config_save_parser.add_argument("--name-translation-format", default=None)
    config_subparsers.add_parser("clear", help="Delete saved local settings.")

    scan_parser = subparsers.add_parser(
        "scan",
        help="Scan a modpack and generate extracted_texts.csv.",
    )
    scan_parser.add_argument("modpack_dir", type=Path, help="Minecraft modpack directory.")
    scan_parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="CSV output path. Defaults to <modpack_dir>/extracted_texts.csv.",
    )
    scan_parser.add_argument(
        "--translate-names",
        action="store_true",
        help="Also extract item/block/entity/fluid display names as lang_name rows.",
    )

    translate_parser = subparsers.add_parser(
        "translate",
        help="Translate extracted_texts.csv and update the translation column.",
    )
    translate_parser.add_argument("modpack_dir", type=Path, help="Minecraft modpack directory.")
    translate_parser.add_argument("--input", "-i", type=Path, default=None, help="Input CSV path.")
    translate_parser.add_argument("--output", "-o", type=Path, default=None, help="Output CSV path.")
    translate_parser.add_argument("--provider", default=None, choices=PROVIDER_CHOICES)
    translate_parser.add_argument("--model", default=None, help="Model name. Required for real providers.")
    translate_parser.add_argument("--api-key", default=None, help="API key. Saved only when --save-config is used.")
    translate_parser.add_argument("--api-key-env", default=None, help="Environment variable that contains the API key.")
    translate_parser.add_argument("--base-url", default=None, help="Override OpenAI-compatible base URL.")
    translate_parser.add_argument("--cache", type=Path, default=None, help="Translation cache JSONL path.")
    translate_parser.add_argument(
        "--speed-mode",
        choices=("safe", "balanced", "fast"),
        default=None,
        help="Batching profile. Defaults to balanced.",
    )
    translate_parser.add_argument("--concurrency", type=int, choices=(1, 2, 3), default=None)
    translate_parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="Legacy alias for --max-batch-items.",
    )
    translate_parser.add_argument("--max-batch-items", type=int, default=None)
    translate_parser.add_argument("--max-input-tokens", type=int, default=None)
    translate_parser.add_argument("--max-output-tokens", type=int, default=None)
    translate_parser.add_argument("--name-translation-format", default=None)
    translate_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Translate at most this many unique uncached segments/API calls.",
    )
    translate_parser.add_argument(
        "--confirm-cost",
        action="store_true",
        help="Allow a real provider to translate without --limit.",
    )
    translate_parser.add_argument("--force", action="store_true", help="Retranslate rows that already have translations.")
    translate_parser.add_argument("--use-config", action="store_true", help="Fill missing provider settings from local config.")
    translate_parser.add_argument("--save-config", action="store_true", help="Save provider settings locally after resolving them.")
    translate_parser.add_argument("--no-progress", action="store_true", help="Disable terminal translation progress output.")
    translate_parser.add_argument(
        "--no-checkpoint",
        action="store_true",
        help="Do not create a CSV checkpoint before writing translations.",
    )

    preview_parser = subparsers.add_parser(
        "preview",
        help="Preview extracted or translated CSV rows.",
    )
    preview_parser.add_argument("csv", type=Path, help="CSV file to preview.")
    preview_parser.add_argument("--limit", type=int, default=20)
    preview_parser.add_argument("--translated-only", action="store_true")
    preview_parser.add_argument("--untranslated-only", action="store_true")

    review_parser = subparsers.add_parser(
        "review",
        help="Generate an HTML side-by-side translation review report.",
    )
    review_parser.add_argument("csv", type=Path, help="CSV file to review.")
    review_parser.add_argument("--output", "-o", type=Path, default=None, help="HTML output path.")
    review_parser.add_argument("--limit", type=int, default=1000, help="Rows to include. Use -1 for all rows.")
    review_parser.add_argument("--issues-only", action="store_true", help="Only include rows with check issues.")

    build_parser_command = subparsers.add_parser(
        "build",
        help="Build resource-pack and config-overlay outputs from translated CSV.",
    )
    build_parser_command.add_argument("modpack_dir", type=Path, help="Minecraft modpack directory.")
    build_parser_command.add_argument("--csv", type=Path, default=None, help="Translated CSV path.")
    build_parser_command.add_argument("--output", "-o", type=Path, default=None, help="Build output directory.")

    check_parser = subparsers.add_parser(
        "check",
        help="Check translated CSV or generated output directory.",
    )
    check_parser.add_argument("target", type=Path, help="CSV file or output directory to check.")
    check_parser.add_argument("--report", type=Path, default=None, help="Report path.")

    status_parser = subparsers.add_parser(
        "status",
        help="Show translation progress and checkpoints for a CSV.",
    )
    status_parser.add_argument("csv", type=Path, help="CSV file to inspect.")

    rollback_parser = subparsers.add_parser(
        "rollback",
        help="Restore a CSV from a previous checkpoint.",
    )
    rollback_parser.add_argument("csv", type=Path, help="CSV file to restore.")
    rollback_parser.add_argument("--checkpoint", default="latest", help="Checkpoint filename/stem, or latest.")

    install_parser = subparsers.add_parser(
        "install",
        help="Install generated packs into a modpack after quality checks and backups.",
    )
    install_parser.add_argument("modpack_dir", type=Path, help="Minecraft modpack directory.")
    install_parser.add_argument("--build-dir", type=Path, default=None, help="Build output directory.")
    install_parser.add_argument("--dry-run", action="store_true", help="Only write an install plan; do not copy files.")
    install_parser.add_argument("--rollback", action="store_true", help="Rollback the latest mc-han install backup.")
    install_parser.add_argument("--backup-dir", type=Path, default=None, help="Backup directory to rollback.")

    all_parser = subparsers.add_parser(
        "all",
        help="Run scan, translate, check, and build.",
    )
    all_parser.add_argument("modpack_dir", type=Path, help="Minecraft modpack directory.")
    all_parser.add_argument("--provider", default=None, choices=PROVIDER_CHOICES)
    all_parser.add_argument("--model", default=None)
    all_parser.add_argument("--api-key", default=None)
    all_parser.add_argument("--api-key-env", default=None)
    all_parser.add_argument("--base-url", default=None)
    all_parser.add_argument("--cache", type=Path, default=None, help="Translation cache JSONL path.")
    all_parser.add_argument("--output", "-o", type=Path, default=None, help="Build output directory.")
    all_parser.add_argument("--limit", type=int, default=None)
    all_parser.add_argument("--speed-mode", choices=("safe", "balanced", "fast"), default=None)
    all_parser.add_argument("--concurrency", type=int, choices=(1, 2, 3), default=None)
    all_parser.add_argument("--batch-size", type=int, default=None, help="Legacy alias for --max-batch-items.")
    all_parser.add_argument("--max-batch-items", type=int, default=None)
    all_parser.add_argument("--max-input-tokens", type=int, default=None)
    all_parser.add_argument("--max-output-tokens", type=int, default=None)
    all_parser.add_argument("--name-translation-format", default=None)
    all_parser.add_argument(
        "--confirm-cost",
        action="store_true",
        help="Allow a real provider to translate without --limit.",
    )
    all_parser.add_argument("--use-config", action="store_true", help="Fill missing provider settings from local config.")
    all_parser.add_argument("--save-config", action="store_true", help="Save provider settings locally after resolving them.")
    all_parser.add_argument("--translate-names", action="store_true", help="Scan and translate display names with English originals.")
    all_parser.add_argument("--no-progress", action="store_true", help="Disable terminal translation progress output.")
    all_parser.add_argument("--no-checkpoint", action="store_true")

    return parser


def run_inspect(modpack_dir: Path, *, json_output: bool = False) -> int:
    inspection = inspect_modpack(modpack_dir)
    if json_output:
        print(json.dumps(inspection.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(format_inspection_summary(inspection))
    return 2 if inspection.validity is InspectionValidity.INVALID else 0


def format_inspection_summary(inspection: ModpackInspection) -> str:
    validity_labels = {
        InspectionValidity.VALID: "有效",
        InspectionValidity.PROBABLE: "可能有效",
        InspectionValidity.INVALID: "无效",
    }
    loader = inspection.loader_name
    if inspection.loader_version != "unknown":
        loader = f"{loader} {inspection.loader_version}"
    available = [item.label for item in inspection.capabilities if item.detected]
    chinese_labels = {
        ChineseResourceStatus.NONE: "未发现",
        ChineseResourceStatus.PARTIAL: "部分存在",
        ChineseResourceStatus.UNKNOWN: "无法完整判断",
    }
    warning_count = sum(1 for item in inspection.messages if item.severity == "warning")
    error_count = sum(1 for item in inspection.messages if item.severity == "error")
    lines = [
        f"整合包：{inspection.display_name}",
        f"状态：{validity_labels[inspection.validity]}",
        f"Minecraft：{inspection.minecraft_version}",
        f"加载器：{loader}",
        f"模组：{inspection.mod_count}",
        f"可汉化内容：{'、'.join(available) if available else '未检测到'}",
        f"已有中文：{chinese_labels[inspection.existing_chinese.status]}",
        f"警告：{warning_count}",
    ]
    if error_count:
        lines.append(f"错误：{error_count}")
    for message in inspection.messages:
        prefix = "错误" if message.severity == "error" else "警告" if message.severity == "warning" else "提示"
        location = f" [{message.location}]" if message.location and message.location != "." else ""
        lines.append(f"- {prefix}{location}：{message.message}")
    return "\n".join(lines)


def run_scan(modpack_dir: Path, output: Path | None = None, *, translate_names: bool = False) -> int:
    modpack_dir = modpack_dir.expanduser().resolve()
    if not modpack_dir.exists():
        print(f"Modpack directory does not exist: {modpack_dir}")
        return 2
    if not modpack_dir.is_dir():
        print(f"Modpack path is not a directory: {modpack_dir}")
        return 2

    output_path = output.expanduser().resolve() if output else modpack_dir / "extracted_texts.csv"
    started_at = perf_counter()
    existing_records = read_extracted_csv(output_path) if output_path.exists() else []
    records = scan_modpack(modpack_dir, translate_names=translate_names)
    if existing_records:
        records = merge_existing_translations(records, existing_records)
    elapsed_seconds = perf_counter() - started_at
    write_extracted_csv(records, output_path)
    report_path = output_path.with_name("scan_report.txt")
    write_scan_report(
        modpack_dir=modpack_dir,
        records=records,
        output_csv=output_path,
        report_path=report_path,
        elapsed_seconds=elapsed_seconds,
    )
    print(f"Scanned {modpack_dir}")
    print(f"Extracted {len(records)} text segment(s)")
    print(f"Wrote {output_path}")
    print(f"Wrote {report_path}")
    return 0


def run_translate(args: argparse.Namespace) -> int:
    modpack_dir = args.modpack_dir.expanduser().resolve()
    input_csv = args.input.expanduser().resolve() if args.input else modpack_dir / "extracted_texts.csv"
    output_csv = args.output.expanduser().resolve() if args.output else input_csv
    cache_path = args.cache.expanduser().resolve() if args.cache else modpack_dir / ".mc-han" / "translation_cache.jsonl"
    options = resolve_translation_settings(args)
    if not real_provider_cost_allowed(options.provider or "mock", options.limit, args.confirm_cost):
        return 2
    translator = create_translator(
        options.provider,
        options.model,
        options.api_key,
        options.api_key_env,
        options.base_url,
    )
    if translator is None:
        return 2
    if getattr(args, "save_config", False):
        saved_path = save_settings(options)
        print(f"Saved config {saved_path}")
    no_checkpoint = getattr(args, "no_checkpoint", False)
    if not no_checkpoint and output_csv.exists():
        checkpoint = create_checkpoint(output_csv, label=f"before-{options.provider}-translate")
        print(f"Checkpoint {checkpoint.path}")
    progress_callback = make_cli_progress_callback(enabled=not getattr(args, "no_progress", False))
    records, translated_count, cache_hits = translate_csv(
        input_csv=input_csv,
        output_csv=output_csv,
        translator=translator,
        cache_path=cache_path,
        batch_size=args.batch_size,
        speed_mode=options.speed_mode or "balanced",
        worker_count=options.worker_count or 1,
        max_batch_items=options.max_batch_items,
        max_input_tokens=options.max_input_tokens,
        max_output_tokens=options.max_output_tokens,
        limit=options.limit,
        force=args.force,
        continue_on_error=True,
        name_translation_format=options.name_translation_format or DEFAULT_NAME_TRANSLATION_FORMAT,
        progress_callback=progress_callback,
    )
    print(f"Translated {translated_count} row(s); cache hits {cache_hits}; total rows {len(records)}")
    print(f"Wrote {output_csv}")
    print(f"Cache {cache_path}")
    return 0


def run_build(modpack_dir: Path, csv_path: Path | None, output: Path | None) -> int:
    modpack_dir = modpack_dir.expanduser().resolve()
    input_csv = csv_path.expanduser().resolve() if csv_path else modpack_dir / "extracted_texts.csv"
    output_dir = output.expanduser().resolve() if output else default_build_dir(modpack_dir)
    stats = build_outputs(modpack_dir=modpack_dir, csv_path=input_csv, output_dir=output_dir)
    print(f"Built {stats['resource_files']} resource file(s) and {stats['config_files']} config file(s)")
    print(f"Wrote {output_dir}")
    return 0


def run_check(target: Path, report: Path | None = None) -> int:
    target = target.expanduser().resolve()
    if target.is_dir():
        issues = check_output_dir(target)
        report_path = report.expanduser().resolve() if report else target / "汉化检查报告.txt"
    else:
        issues = check_csv(target)
        report_path = report.expanduser().resolve() if report else target.with_name("汉化检查报告.txt")
    write_quality_report(issues, report_path)
    errors = sum(1 for issue in issues if issue.severity == "error")
    warnings = sum(1 for issue in issues if issue.severity == "warning")
    print(f"Quality check: {errors} error(s), {warnings} warning(s)")
    print(f"Wrote {report_path}")
    return 1 if errors else 0


def run_install(
    modpack_dir: Path,
    build_dir: Path | None = None,
    *,
    dry_run: bool = False,
    rollback: bool = False,
    backup_dir: Path | None = None,
) -> int:
    modpack_dir = modpack_dir.expanduser().resolve()
    resolved_build_dir = build_dir.expanduser().resolve() if build_dir else default_build_dir(modpack_dir)
    if rollback:
        try:
            result = rollback_install(
                modpack_dir=modpack_dir,
                backup_dir=backup_dir.expanduser().resolve() if backup_dir else None,
            )
        except RuntimeError as error:
            print(str(error))
            return 1
        print(f"Restored {result.restored_files} file(s)")
        print(f"Removed {result.removed_files} newly installed file(s)")
        print(f"Backup directory {result.backup_dir}")
        return 0
    if dry_run:
        plan = plan_install_outputs(modpack_dir=modpack_dir, build_dir=resolved_build_dir)
        report_path = resolved_build_dir / "install_plan.txt"
        write_install_plan_report(plan, report_path)
        print(f"Install plan: {plan.total_files} file(s), {plan.overwrite_files} overwrite(s), {plan.new_files} new")
        print(f"Wrote {report_path}")
        return 0
    try:
        result = install_outputs(modpack_dir=modpack_dir, build_dir=resolved_build_dir)
    except RuntimeError as error:
        print(str(error))
        return 1
    print(f"Installed {result.installed_files} file(s)")
    print(f"Backed up {result.backed_up_files} existing file(s)")
    print(f"Backup directory {result.backup_dir}")
    print(f"Manifest {result.manifest_path}")
    return 0


def run_all(args: argparse.Namespace) -> int:
    modpack_dir = args.modpack_dir.expanduser().resolve()
    build_dir = args.output.expanduser().resolve() if args.output else default_build_dir(modpack_dir)
    csv_path = build_dir / "extracted_texts.csv"
    saved = load_settings() if getattr(args, "use_config", False) else UserSettings()
    translate_names = bool(getattr(args, "translate_names", False) or saved.translate_names)
    scan_code = run_scan(modpack_dir, csv_path, translate_names=translate_names)
    if scan_code:
        return scan_code
    translate_args = argparse.Namespace(
        modpack_dir=modpack_dir,
        input=csv_path,
        output=csv_path,
        provider=args.provider,
        model=args.model,
        api_key=args.api_key,
        api_key_env=args.api_key_env,
        base_url=args.base_url,
        cache=getattr(args, "cache", None),
        speed_mode=getattr(args, "speed_mode", None),
        concurrency=getattr(args, "concurrency", None),
        batch_size=getattr(args, "batch_size", None),
        max_batch_items=getattr(args, "max_batch_items", None),
        max_input_tokens=getattr(args, "max_input_tokens", None),
        max_output_tokens=getattr(args, "max_output_tokens", None),
        name_translation_format=getattr(args, "name_translation_format", None),
        limit=args.limit,
        confirm_cost=args.confirm_cost,
        no_checkpoint=args.no_checkpoint,
        force=False,
        use_config=getattr(args, "use_config", False),
        save_config=getattr(args, "save_config", False),
        no_progress=getattr(args, "no_progress", False),
    )
    translate_code = run_translate(translate_args)
    if translate_code:
        return translate_code
    csv_check_code = run_check(csv_path, build_dir / "汉化检查报告.txt")
    if csv_check_code:
        print("Check failed; build skipped.")
        return csv_check_code
    build_code = run_build(modpack_dir, csv_path, build_dir)
    if build_code:
        return build_code
    return run_check(build_dir, build_dir / "汉化检查报告.txt")


def run_preview(csv_path: Path, limit: int, translated_only: bool, untranslated_only: bool) -> int:
    print(
        build_preview(
            csv_path.expanduser().resolve(),
            limit=limit,
            translated_only=translated_only,
            untranslated_only=untranslated_only,
        )
    )
    return 0


def run_review(csv_path: Path, output: Path | None, limit: int, issues_only: bool) -> int:
    csv_path = csv_path.expanduser().resolve()
    output_path = output.expanduser().resolve() if output else csv_path.with_name("translation_review.html")
    stats = write_review_report(
        csv_path=csv_path,
        output_path=output_path,
        limit=None if limit < 0 else limit,
        issues_only=issues_only,
    )
    print(
        f"Review report: {stats.displayed_rows}/{stats.total_rows} row(s), "
        f"{stats.errors} error(s), {stats.warnings} warning(s)"
    )
    print(f"Wrote {output_path}")
    return 0


def run_status(csv_path: Path) -> int:
    csv_path = csv_path.expanduser().resolve()
    status = csv_status(csv_path)
    print(f"CSV {csv_path}")
    print(f"Rows {status.total_rows}")
    print(f"Translated {status.translated_rows}")
    print(f"Missing {status.missing_rows}")
    print(f"Errors {status.errors}")
    print(f"Warnings {status.warnings}")
    checkpoints = list_checkpoints(csv_path)
    print(f"Checkpoints {len(checkpoints)}")
    for info in checkpoints[-5:]:
        print(f"  {info.path.name}  {info.label}")
    return 0


def run_rollback(csv_path: Path, checkpoint: str) -> int:
    csv_path = csv_path.expanduser().resolve()
    restored = rollback_checkpoint(csv_path, checkpoint=checkpoint)
    print(f"Restored {csv_path}")
    print(f"From {restored.path}")
    return 0


def run_config(args: argparse.Namespace) -> int:
    if args.config_command == "show":
        settings = load_settings()
        print(f"Config {config_path()}")
        print(f"Provider {settings.provider or ''}")
        print(f"Model {settings.model or ''}")
        print(f"Base URL {settings.base_url or ''}")
        print(f"API key env {settings.api_key_env or ''}")
        print(f"API key {masked_api_key(settings.api_key)}")
        print(f"Limit {settings.limit or ''}")
        print(f"Speed mode {settings.speed_mode or ''}")
        print(f"Concurrency {settings.worker_count or ''}")
        print(f"Max batch items {settings.max_batch_items or ''}")
        print(f"Max input tokens {settings.max_input_tokens or ''}")
        print(f"Max output tokens {settings.max_output_tokens or ''}")
        print(f"Translate names {settings.translate_names if settings.translate_names is not None else ''}")
        print(f"Name translation format {settings.name_translation_format or ''}")
        return 0
    if args.config_command == "save":
        settings = merge_settings(
            load_settings(),
            provider=args.provider,
            model=args.model,
            api_key=args.api_key,
            api_key_env=args.api_key_env,
            base_url=args.base_url,
            limit=args.limit,
            speed_mode=args.speed_mode,
            worker_count=args.concurrency,
            max_batch_items=args.max_batch_items,
            max_input_tokens=args.max_input_tokens,
            max_output_tokens=args.max_output_tokens,
            translate_names=args.translate_names,
            name_translation_format=args.name_translation_format,
        )
        saved_path = save_settings(settings)
        print(f"Saved config {saved_path}")
        return 0
    if args.config_command == "clear":
        deleted = clear_settings()
        print(f"Deleted config {config_path()}" if deleted else f"No config found at {config_path()}")
        return 0
    return 2


def resolve_translation_settings(args: argparse.Namespace) -> UserSettings:
    saved = load_settings() if getattr(args, "use_config", False) else UserSettings()
    limit = getattr(args, "limit", None)
    speed_mode = getattr(args, "speed_mode", None)
    worker_count = getattr(args, "concurrency", None)
    max_batch_items = getattr(args, "max_batch_items", None) or getattr(args, "batch_size", None)
    return UserSettings(
        provider=getattr(args, "provider", None) or saved.provider or "mock",
        model=getattr(args, "model", None) or saved.model,
        api_key=getattr(args, "api_key", None) or saved.api_key,
        api_key_env=getattr(args, "api_key_env", None) or saved.api_key_env,
        base_url=getattr(args, "base_url", None) or saved.base_url,
        limit=limit if limit is not None else saved.limit,
        speed_mode=speed_mode or saved.speed_mode,
        worker_count=worker_count if worker_count is not None else saved.worker_count,
        max_batch_items=max_batch_items if max_batch_items is not None else saved.max_batch_items,
        max_input_tokens=getattr(args, "max_input_tokens", None) or saved.max_input_tokens,
        max_output_tokens=getattr(args, "max_output_tokens", None) or saved.max_output_tokens,
        translate_names=getattr(args, "translate_names", None)
        if getattr(args, "translate_names", None) is not None
        else saved.translate_names,
        name_translation_format=getattr(args, "name_translation_format", None) or saved.name_translation_format,
    )


def make_cli_progress_callback(*, enabled: bool) -> Callable[[TranslationProgress], None] | None:
    if not enabled or not sys.stdout.isatty():
        return None

    def callback(progress: TranslationProgress) -> None:
        total = max(1, progress.total_rows)
        completed = min(progress.completed_rows, total)
        width = 28
        filled = int(width * completed / total)
        bar = "#" * filled + "-" * (width - filled)
        percent = int(100 * completed / total)
        sys.stdout.write(
            "\r"
            f"翻译进度 [{bar}] {completed}/{progress.total_rows} {percent:3d}% "
            f"API批次 {progress.api_batches_done}/{progress.api_batches_total} "
            f"API新 {progress.api_translated_rows} "
            f"缓存/复用 {progress.cache_hits} "
            f"失败 {progress.failed_rows} "
            f"剩余 {progress.remaining_rows} "
            f"ETA {format_eta(progress.eta_seconds)}"
        )
        sys.stdout.flush()
        if progress.total_rows == 0 or progress.completed_rows >= progress.total_rows:
            sys.stdout.write("\n")
            sys.stdout.flush()

    return callback


def real_provider_cost_allowed(provider: str, limit: int | None, confirm_cost: bool) -> bool:
    if provider == "mock" or limit is not None or confirm_cost:
        return True
    print(
        "Real providers require --limit for a trial run or --confirm-cost to translate every pending row. "
        "This guard helps prevent accidental large API bills."
    )
    return False


def format_eta(seconds: float | None) -> str:
    if seconds is None:
        return "--"
    seconds = max(0, int(seconds))
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def create_translator(
    provider: str,
    model: str | None,
    api_key: str | None,
    api_key_env: str | None,
    base_url: str | None,
) -> object | None:
    provider = provider or "mock"
    if provider == "mock":
        return MockTranslator()
    resolved_model = model
    if not resolved_model:
        print("A --model value is required for real translation providers.")
        return None
    if provider == CUSTOM_PROVIDER:
        preset_base_url = None
        default_env_name = "MC_HAN_API_KEY"
    else:
        if provider not in PROVIDER_PRESETS:
            print(f"Unknown provider: {provider}")
            return None
        preset = PROVIDER_PRESETS[provider]
        preset_base_url = preset.base_url
        default_env_name = preset.api_key_env
    resolved_base_url = base_url or preset_base_url
    if not resolved_base_url:
        print("Custom providers require --base-url.")
        return None
    env_name = api_key_env or default_env_name
    resolved_api_key = api_key or os.environ.get(env_name)
    if not resolved_api_key:
        print(f"Missing API key. Pass --api-key or set {env_name}.")
        return None
    provider_name = provider if provider != CUSTOM_PROVIDER else f"{CUSTOM_PROVIDER}:{resolved_base_url.rstrip('/')}"
    return OpenAICompatibleTranslator(
        provider_name=provider_name,
        model=resolved_model,
        api_key=resolved_api_key,
        base_url=resolved_base_url,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "scan":
        return run_scan(args.modpack_dir, args.output, translate_names=args.translate_names)
    if args.command == "gui":
        from .gui import run_gui

        return run_gui()
    if args.command == "inspect":
        return run_inspect(args.modpack_dir, json_output=args.json_output)
    if args.command == "config":
        return run_config(args)
    if args.command == "translate":
        return run_translate(args)
    if args.command == "preview":
        return run_preview(args.csv, args.limit, args.translated_only, args.untranslated_only)
    if args.command == "review":
        return run_review(args.csv, args.output, args.limit, args.issues_only)
    if args.command == "build":
        return run_build(args.modpack_dir, args.csv, args.output)
    if args.command == "check":
        return run_check(args.target, args.report)
    if args.command == "status":
        return run_status(args.csv)
    if args.command == "rollback":
        return run_rollback(args.csv, args.checkpoint)
    if args.command == "install":
        return run_install(
            args.modpack_dir,
            args.build_dir,
            dry_run=args.dry_run,
            rollback=args.rollback,
            backup_dir=args.backup_dir,
        )
    if args.command == "all":
        return run_all(args)

    parser.error(f"Unknown command: {args.command}")
    return 2
