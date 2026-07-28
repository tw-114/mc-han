from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import replace
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from mc_han.core.project import project_paths
from mc_han.csv_store import read_extracted_csv
from mc_han.models import ExtractedText
from mc_han.qt.translation_config_view_models import (
    TranslationSessionConfig,
    create_translator,
)
from mc_han.translator.engine import (
    TranslationItemCompleted,
    TranslationItemFailed,
    TranslationProgress,
    translate_csv,
)
from mc_han.usage.ledger import UsageLedger
from mc_han.usage.service import UsageQueryService
from mc_han.workflow.scan_models import (
    CATEGORY_DEFINITIONS,
    TRANSLATABLE_CATEGORY_ORDER,
    ScanSelectionState,
    category_for_record,
)
from mc_han.workflow.trial_models import (
    TrialProgressEvent,
    TrialProgressStage,
    TrialSampleResult,
    TrialSampleStatus,
    TrialTranslationResult,
)


DEFAULT_TRIAL_SAMPLE_COUNT = 10
MIN_TRIAL_SAMPLE_COUNT = 8
MAX_TRIAL_SAMPLE_COUNT = 12

TrialProgressCallback = Callable[[TrialProgressEvent], None]
TranslatorFactory = Callable[[TranslationSessionConfig], object]


class TrialTranslationError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(code)
        self.code = code
        self.message = message


def prepare_trial_samples(
    modpack_dir: Path,
    selection: ScanSelectionState,
    *,
    sample_count: int = DEFAULT_TRIAL_SAMPLE_COUNT,
) -> tuple[TrialSampleResult, ...]:
    paths = project_paths(modpack_dir)
    try:
        records = read_extracted_csv(paths.extracted_csv)
    except (OSError, UnicodeError, ValueError) as error:
        raise TrialTranslationError(
            "trial_source_unreadable",
            "无法读取扫描清单，请返回扫描页面重新扫描。",
        ) from error
    return select_trial_samples(
        records,
        selection,
        sample_count=sample_count,
    )


def select_trial_samples(
    records: Iterable[ExtractedText],
    selection: ScanSelectionState,
    *,
    sample_count: int = DEFAULT_TRIAL_SAMPLE_COUNT,
) -> tuple[TrialSampleResult, ...]:
    if type(sample_count) is not int:
        raise TypeError("sample_count must be an integer")
    if sample_count < MIN_TRIAL_SAMPLE_COUNT or sample_count > MAX_TRIAL_SAMPLE_COUNT:
        raise ValueError(
            f"sample_count must be between {MIN_TRIAL_SAMPLE_COUNT} "
            f"and {MAX_TRIAL_SAMPLE_COUNT}"
        )
    eligible = tuple(
        record
        for record in selection.selected_records(records)
        if record.original.strip() and not record.skip_status.strip()
    )
    ordered = tuple(sorted(eligible, key=_sample_sort_key))
    target_count = min(sample_count, len(ordered))
    if target_count == 0:
        return ()

    selected: list[ExtractedText] = []
    selected_ids: set[str] = set()
    by_category: dict[object, list[ExtractedText]] = {}
    for record in ordered:
        by_category.setdefault(category_for_record(record), []).append(record)
    for category_id in TRANSLATABLE_CATEGORY_ORDER:
        category_records = by_category.get(category_id, ())
        if category_records and len(selected) < target_count:
            record = category_records[0]
            selected.append(record)
            selected_ids.add(record.id)
    for record in ordered:
        if len(selected) >= target_count:
            break
        if record.id not in selected_ids:
            selected.append(record)
            selected_ids.add(record.id)
    return tuple(_sample_from_record(record) for record in selected)


def run_trial_translation(
    modpack_dir: Path,
    config: TranslationSessionConfig,
    samples: tuple[TrialSampleResult, ...],
    *,
    task_id: str | None = None,
    target_ids: frozenset[str] | None = None,
    progress: TrialProgressCallback | None = None,
    translator_factory: TranslatorFactory = create_translator,
) -> TrialTranslationResult:
    paths = project_paths(modpack_dir)
    if not paths.extracted_csv.is_file():
        raise TrialTranslationError(
            "trial_source_missing",
            "扫描清单不存在，请返回扫描页面重新扫描。",
        )
    resolved_task_id = task_id or f"trial-{uuid4().hex}"
    all_ids = frozenset(item.text_id for item in samples)
    selected_ids = all_ids if target_ids is None else frozenset(target_ids)
    if not selected_ids or not selected_ids.issubset(all_ids):
        raise TrialTranslationError(
            "trial_selection_invalid",
            "没有可重试的失败样本。",
        )

    _emit_progress(
        progress,
        TrialProgressStage.PREPARING,
        "正在准备试译请求",
        0,
        len(selected_ids),
    )
    try:
        translator = translator_factory(config)
    except (TypeError, ValueError) as error:
        raise TrialTranslationError(
            "trial_client_invalid",
            "无法创建翻译客户端，请返回检查服务配置。",
        ) from error

    status_by_id: dict[str, tuple[TrialSampleStatus, bool]] = {}
    completed = 0

    def handle_event(event: object) -> None:
        nonlocal completed
        if isinstance(event, TranslationItemCompleted):
            if event.text_id not in selected_ids:
                return
            status_by_id[event.text_id] = (
                TrialSampleStatus.SUCCESS,
                event.status == "缓存复用",
            )
            completed += 1
        elif isinstance(event, TranslationItemFailed):
            if event.text_id not in selected_ids:
                return
            status_by_id[event.text_id] = (TrialSampleStatus.FAILED, False)
            completed += 1
        elif not isinstance(event, TranslationProgress):
            return
        _emit_progress(
            progress,
            TrialProgressStage.TRANSLATING,
            "正在进行小批量试译",
            min(completed, len(selected_ids)),
            len(selected_ids),
        )

    started = perf_counter()
    try:
        updated, _translated_count, _cache_hits = translate_csv(
            input_csv=paths.extracted_csv,
            output_csv=paths.extracted_csv,
            translator=translator,
            cache_path=paths.translation_cache_jsonl,
            sqlite_cache_path=paths.translations_sqlite,
            usage_ledger_path=paths.usage_sqlite,
            usage_task_id=resolved_task_id,
            target_ids=set(selected_ids),
            worker_count=config.concurrency,
            max_batch_items=config.batch_size,
            continue_on_error=True,
            event_callback=handle_event,
        )
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as error:
        raise TrialTranslationError(
            "trial_translation_failed",
            "试译未能完成，已保存的成功结果仍会保留，可稍后重试。",
        ) from error
    elapsed = perf_counter() - started
    updated_by_id = {record.id: record for record in updated}
    result_samples = tuple(
        _updated_sample(
            sample,
            updated_by_id.get(sample.text_id),
            status_by_id.get(sample.text_id),
            targeted=sample.text_id in selected_ids,
        )
        for sample in samples
    )
    try:
        with UsageLedger(paths.usage_sqlite) as ledger:
            usage = UsageQueryService(ledger).task_summary(resolved_task_id)
    except (OSError, sqlite3.Error) as error:
        raise TrialTranslationError(
            "trial_usage_unreadable",
            "试译结果已保存，但本次 Token 和费用记录暂时无法读取。",
        ) from error
    _emit_progress(
        progress,
        TrialProgressStage.COMPLETED,
        "试译完成",
        len(selected_ids),
        len(selected_ids),
    )
    return TrialTranslationResult(
        samples=result_samples,
        usage=usage,
        elapsed_seconds=elapsed,
        task_id=resolved_task_id,
    )


def _sample_from_record(record: ExtractedText) -> TrialSampleResult:
    category_id = category_for_record(record)
    definition = CATEGORY_DEFINITIONS[category_id]
    return TrialSampleResult(
        text_id=record.id,
        original=record.original,
        translation=record.translation,
        category_id=category_id,
        category_title=definition.title,
        source=record.file_path,
        status=(
            TrialSampleStatus.SUCCESS
            if record.translation.strip()
            else TrialSampleStatus.PENDING
        ),
        from_cache=bool(record.translation.strip()),
    )


def _updated_sample(
    sample: TrialSampleResult,
    record: ExtractedText | None,
    event_status: tuple[TrialSampleStatus, bool] | None,
    *,
    targeted: bool,
) -> TrialSampleResult:
    if not targeted:
        return sample
    translation = record.translation.strip() if record is not None else ""
    if event_status is not None:
        status, from_cache = event_status
    elif translation:
        status, from_cache = TrialSampleStatus.SUCCESS, True
    else:
        status, from_cache = TrialSampleStatus.FAILED, False
    return replace(
        sample,
        translation=translation,
        status=status,
        from_cache=from_cache,
    )


def _sample_sort_key(record: ExtractedText) -> tuple[object, ...]:
    category_order = {
        category_id: index
        for index, category_id in enumerate(TRANSLATABLE_CATEGORY_ORDER)
    }
    return (
        category_order.get(
            category_for_record(record),
            len(category_order),
        ),
        bool(record.translation.strip()),
        record.container.casefold(),
        record.file_path.casefold(),
        record.key_path.casefold(),
        record.id,
    )


def _emit_progress(
    callback: TrialProgressCallback | None,
    stage: TrialProgressStage,
    message: str,
    completed: int,
    total: int,
) -> None:
    if callback is not None:
        callback(TrialProgressEvent(stage, message, completed, total))
