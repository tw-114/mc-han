from __future__ import annotations

from collections import deque
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
import re
import sqlite3
from pathlib import Path
from threading import Event, Lock
from time import perf_counter, sleep
from typing import Callable
from uuid import uuid4

from mc_han.csv_store import read_extracted_csv, write_extracted_csv
from mc_han.config import DEFAULT_NAME_TRANSLATION_FORMAT
from mc_han.models import ExtractedText
from mc_han.quality.checks import extract_resource_ids
from mc_han.quality.markdown import fenced_code_blocks_closed
from mc_han.quality.placeholders import placeholders_match
from mc_han.services.provenance import TranslationProvenanceStore
from mc_han.usage.ledger import UsageLedger
from mc_han.usage.models import (
    ApiAttemptUsage,
    PricingProfile,
    TokenUsage,
    UsageCategoryCount,
    UsageOutcome,
)
from mc_han.usage.pricing import estimate_cost, select_pricing_profile
from mc_han.workflow.scan_models import category_for_record
from mc_han.workflow.provenance import TranslationSource

from .base import TranslationSegment, Translator
from .batching import PendingGroup, build_token_batches, make_pending_group, resolve_speed_mode
from .cache import TranslationCache, make_reuse_key
from .names import is_name_source, name_translation_keeps_english, normalize_name_translation
from .sqlite_cache import SQLiteTranslationCache
from .usage import (
    ProviderAttemptError,
    ProviderAttemptResult,
    UsageNormalizationResult,
    sanitize_provider_request_id,
)


@dataclass(frozen=True)
class TranslationProgress:
    completed_rows: int
    total_rows: int
    translated_rows: int
    cache_hits: int
    api_translated_rows: int
    failed_rows: int
    remaining_rows: int
    api_batches_done: int
    api_batches_total: int
    eta_seconds: float | None
    message: str


class TranslationValidationError(RuntimeError):
    """Raised when a provider response breaks protected structure."""


@dataclass(frozen=True)
class TranslationStarted:
    text_id: str
    original: str
    file_path: str
    source_type: str = ""
    status: str = "请求 API 中"
    translation: str = ""
    batch_index: int = 0


@dataclass(frozen=True)
class TranslationBatchStarted:
    batch_index: int
    batch_total: int
    item_count: int
    file_paths: tuple[str, ...]


@dataclass(frozen=True)
class TranslationItemCompleted:
    text_id: str
    original: str
    translation: str
    status: str
    file_path: str = ""
    source_type: str = ""
    batch_index: int = 0


@dataclass(frozen=True)
class TranslationItemFailed:
    text_id: str
    original: str
    error: str
    file_path: str = ""
    source_type: str = ""
    batch_index: int = 0


@dataclass(frozen=True)
class TranslationBatchCompleted:
    batch_index: int
    api_time: float
    translated_count: int
    failed_count: int
    check_result: str
    cached_written: bool


@dataclass(frozen=True)
class TranslationTaskDiagnostic:
    code: str
    message: str
    severity: str = "warning"


def translate_csv(
    *,
    input_csv: Path,
    output_csv: Path,
    translator: Translator,
    cache_path: Path,
    batch_size: int | None = None,
    speed_mode: str = "balanced",
    worker_count: int = 1,
    max_batch_items: int | None = None,
    max_input_tokens: int | None = None,
    max_output_tokens: int | None = None,
    limit: int | None = None,
    force: bool = False,
    force_ids: set[str] | None = None,
    progress_callback: Callable[[TranslationProgress], None] | None = None,
    sqlite_cache_path: Path | None = None,
    pause_event: Event | None = None,
    stop_event: Event | None = None,
    continue_on_error: bool = False,
    max_retries: int = 2,
    retry_delay_seconds: float = 1.0,
    name_translation_format: str = DEFAULT_NAME_TRANSLATION_FORMAT,
    event_callback: Callable[[object], None] | None = None,
    target_ids: set[str] | None = None,
    usage_ledger: UsageLedger | None = None,
    usage_ledger_path: Path | None = None,
    usage_task_id: str | None = None,
    pricing_profiles: tuple[PricingProfile, ...] = (),
    provenance_path: Path | None = None,
    rule_version: str = "",
) -> tuple[list[ExtractedText], int, int]:
    config = resolve_speed_mode(
        speed_mode,
        max_items=max_batch_items or batch_size,
        max_input_tokens=max_input_tokens,
        max_output_tokens=max_output_tokens,
    )
    worker_count = min(3, max(1, int(worker_count)))
    max_retries = max(0, int(max_retries))
    retry_delay_seconds = max(0.0, float(retry_delay_seconds))

    records = read_extracted_csv(input_csv)
    updated = list(records)
    cache = TranslationCache(cache_path)
    sqlite_cache = SQLiteTranslationCache(sqlite_cache_path) if sqlite_cache_path else None
    provenance_store = (
        TranslationProvenanceStore(provenance_path)
        if provenance_path is not None
        else None
    )
    owns_usage_ledger = False
    is_network_provider = bool(
        getattr(translator, "is_network_provider", False)
    )
    if is_network_provider and usage_ledger is None:
        resolved_usage_path = usage_ledger_path or infer_usage_ledger_path(
            input_csv=input_csv,
            output_csv=output_csv,
            sqlite_cache_path=sqlite_cache_path,
        )
        try:
            usage_ledger = UsageLedger(resolved_usage_path)
        except (OSError, sqlite3.Error):
            if sqlite_cache:
                sqlite_cache.close()
            raise
        owns_usage_ledger = True
    task_id = safe_usage_label(
        usage_task_id or uuid4().hex,
        fallback="task",
    )

    update_lock = Lock()
    progress_lock = Lock()
    usage_diagnostic_lock = Lock()
    emitted_usage_diagnostics: set[str] = set()
    recent_batch_rates: deque[float] = deque(maxlen=10)

    translated_count = 0
    cache_hits = 0
    failed_rows = 0
    already_translated_rows = 0
    completed_rows = 0
    translated_rows = 0
    api_batches_done = 0
    api_batches_total = 0
    local_validation_failed_items = 0

    pending_order: list[str] = []
    pending_groups: dict[str, list[tuple[int, ExtractedText]]] = {}

    def record_provenance(
        record: ExtractedText,
        translation: str,
        source: TranslationSource,
    ) -> None:
        if provenance_store is None:
            return
        try:
            provenance_store.record_translation(
                record,
                translation,
                source=source,
                provider=translator.provider_name,
                model=translator.model,
                rule_version=rule_version,
            )
        except (OSError, sqlite3.Error, ValueError):
            emit_event(
                event_callback,
                TranslationTaskDiagnostic(
                    code="provenance_write_failed",
                    message="译文已保存，但来源记录暂时未能更新。",
                ),
            )

    try:
        for index, record in enumerate(records):
            if not record.original.strip():
                continue
            if target_ids is not None and record.id not in target_ids:
                continue
            force_record = force or (
                force_ids is not None and record.id in force_ids
            )
            if record.translation and not force_record:
                already_translated_rows += 1
                continue

            cached = (
                sqlite_cache.get(record)
                if sqlite_cache and not force_record
                else None
            )
            if cached is None:
                cached = (
                    cache.get(
                        provider=translator.provider_name,
                        model=translator.model,
                        original=record.original,
                    )
                    if not force_record
                    else None
                )
            if cached is not None and not force_record:
                updated[index] = replace(record, translation=cached, note="")
                if sqlite_cache:
                    sqlite_cache.set(
                        record,
                        translation=cached,
                        provider=translator.provider_name,
                        model=translator.model,
                    )
                cache_hits += 1
                record_provenance(
                    record,
                    cached,
                    TranslationSource.LOCAL_MEMORY,
                )
                emit_event(
                    event_callback,
                    TranslationItemCompleted(
                        text_id=record.id,
                        original=record.original,
                        translation=cached,
                        status="缓存复用",
                        file_path=record.file_path,
                        source_type=record.source_type,
                    ),
                )
                continue

            reuse_key = make_reuse_key(
                provider=translator.provider_name,
                model=translator.model,
                original=record.original,
            )
            if reuse_key in pending_groups:
                pending_groups[reuse_key].append((index, record))
                continue
            if limit is not None and len(pending_order) >= limit:
                continue
            pending_order.append(reuse_key)
            pending_groups[reuse_key] = [(index, record)]

        pending_items = [make_pending_group(reuse_key, pending_groups[reuse_key]) for reuse_key in pending_order]
        batches = build_token_batches(pending_items, config)
        api_batches_total = len(batches)
        total_rows = sum(
            1
            for record in records
            if record.original.strip() and (target_ids is None or record.id in target_ids)
        )
        completed_rows = already_translated_rows + cache_hits
        translated_rows = already_translated_rows + cache_hits

        def emit_usage_diagnostic(code: str, message: str) -> None:
            with usage_diagnostic_lock:
                if code in emitted_usage_diagnostics:
                    return
                emitted_usage_diagnostics.add(code)
            emit_event(
                event_callback,
                TranslationTaskDiagnostic(
                    code=code,
                    message=message,
                ),
            )

        def update_usage_task_stats() -> None:
            if usage_ledger is None:
                return
            with progress_lock:
                snapshot_translated = translated_rows
                snapshot_cache_hits = cache_hits
            try:
                usage_ledger.update_task_stats(
                    task_id=task_id,
                    total_items=total_rows,
                    reused_items=snapshot_cache_hits,
                    avoided_api_items=already_translated_rows + snapshot_cache_hits,
                    remaining_items=remaining_rows(
                        total_rows,
                        snapshot_translated,
                    ),
                    updated_at=utc_now_iso(),
                )
            except (OSError, sqlite3.Error):
                emit_usage_diagnostic(
                    "usage_ledger_write_failed",
                    "用量账本保存失败，账本可能不完整；已完成的翻译结果仍会保存在本地。",
                )

        update_usage_task_stats()

        def emit_locked(message: str) -> None:
            with progress_lock:
                rows_left = remaining_rows(total_rows, completed_rows)
                emit_progress(
                    progress_callback,
                    completed_rows=completed_rows,
                    total_rows=total_rows,
                    translated_rows=translated_rows,
                    cache_hits=cache_hits,
                    failed_rows=failed_rows,
                    api_translated_rows=translated_count,
                    remaining_rows=rows_left,
                    api_batches_done=api_batches_done,
                    api_batches_total=api_batches_total,
                    eta_seconds=estimate_eta_seconds(
                        remaining_rows=rows_left,
                        recent_batch_rates=recent_batch_rates,
                    ),
                    message=message,
                )
                emit_event(
                    event_callback,
                    TranslationProgress(
                        completed_rows=completed_rows,
                        total_rows=total_rows,
                        translated_rows=translated_rows,
                        cache_hits=cache_hits,
                        api_translated_rows=translated_count,
                        failed_rows=failed_rows,
                        remaining_rows=rows_left,
                        api_batches_done=api_batches_done,
                        api_batches_total=api_batches_total,
                        eta_seconds=estimate_eta_seconds(
                            remaining_rows=rows_left,
                            recent_batch_rates=recent_batch_rates,
                        ),
                        message=message,
                    ),
                )

        def reserve_extra_batch() -> None:
            nonlocal api_batches_total
            with progress_lock:
                api_batches_total += 1

        def mark_api_attempt_done() -> None:
            nonlocal api_batches_done
            with progress_lock:
                api_batches_done += 1

        def process_batch(batch: list[PendingGroup], *, planned: bool = True, batch_index: int = 0) -> None:
            nonlocal local_validation_failed_items
            if not batch:
                return
            usage_batch_id = uuid4().hex
            if not planned:
                reserve_extra_batch()
                batch_index = api_batches_total
            if not wait_until_resumed(pause_event, stop_event):
                return
            if stop_event and stop_event.is_set():
                return

            segments = [segment_from_group(group) for group in batch]
            started_at = perf_counter()
            last_error: Exception | None = None
            split_on_failure = True
            for attempt in range(max_retries + 1):
                if attempt > 0:
                    reserve_extra_batch()
                    batch_index = api_batches_total
                    if not wait_before_retry(retry_delay_seconds * attempt, pause_event, stop_event):
                        return
                if not wait_until_resumed(pause_event, stop_event):
                    return
                if stop_event and stop_event.is_set():
                    return
                emit_event(
                    event_callback,
                    TranslationBatchStarted(
                        batch_index=batch_index,
                        batch_total=api_batches_total,
                        item_count=sum(group.row_count for group in batch),
                        file_paths=tuple(sorted({group.representative.file_path for group in batch})),
                    ),
                )
                for segment in segments:
                    emit_event(
                        event_callback,
                        TranslationStarted(
                            text_id=segment.id,
                            original=segment.text,
                            file_path=segment.file_path,
                            source_type=segment.source_type,
                            status="请求 API 中",
                            batch_index=batch_index,
                        ),
                    )
                attempt_started_at = utc_now_iso()
                attempt_started_perf = perf_counter()
                attempt_result: ProviderAttemptResult | None = None
                try:
                    attempt_result = translate_batch_with_usage(
                        translator,
                        segments,
                    )
                    translations = list(attempt_result.translations)
                    emit_checking_events(event_callback, segments, batch_index=batch_index)
                    translations = normalize_batch_translations(
                        segments,
                        translations,
                        name_translation_format=name_translation_format,
                    )
                    validate_batch_translations(segments, translations)
                except TranslationValidationError as error:
                    network_started = bool(
                        attempt_result
                        and attempt_result.network_attempt_started
                    )
                    if network_started:
                        record_usage_attempt(
                            ledger=usage_ledger,
                            translator=translator,
                            task_id=task_id,
                            batch_id=usage_batch_id,
                            attempt_number=attempt + 1,
                            batch=batch,
                            started_at=attempt_started_at,
                            latency_ms=elapsed_ms(attempt_started_perf),
                            outcome=UsageOutcome.LOCAL_VALIDATION_FAILED,
                            retryable=False,
                            stable_error_code="local_translation_validation_failed",
                            attempt_result=attempt_result,
                            pricing_profiles=pricing_profiles,
                            write_failure_callback=lambda: emit_usage_diagnostic(
                                "usage_ledger_write_failed",
                                "用量账本保存失败，账本可能不完整；已完成的翻译结果仍会保存在本地。",
                            ),
                        )
                        mark_api_attempt_done()
                    with progress_lock:
                        local_validation_failed_items += sum(
                            group.row_count for group in batch
                        )
                    update_usage_task_stats()
                    last_error = error
                    break
                except ProviderAttemptError as error:
                    if error.network_attempt_started:
                        record_usage_attempt(
                            ledger=usage_ledger,
                            translator=translator,
                            task_id=task_id,
                            batch_id=usage_batch_id,
                            attempt_number=attempt + 1,
                            batch=batch,
                            started_at=attempt_started_at,
                            latency_ms=elapsed_ms(attempt_started_perf),
                            outcome=error.outcome,
                            retryable=error.retryable,
                            stable_error_code=error.stable_error_code,
                            attempt_result=ProviderAttemptResult(
                                translations=(),
                                usage=error.usage,
                                provider_request_id=error.provider_request_id,
                                network_attempt_started=True,
                            ),
                            pricing_profiles=pricing_profiles,
                            write_failure_callback=lambda: emit_usage_diagnostic(
                                "usage_ledger_write_failed",
                                "用量账本保存失败，账本可能不完整；已完成的翻译结果仍会保存在本地。",
                            ),
                        )
                        mark_api_attempt_done()
                    last_error = error
                    if (
                        not error.network_attempt_started
                        or error.outcome is UsageOutcome.CANCELLED
                    ):
                        split_on_failure = False
                        break
                except CancelledError as error:
                    # Without a structured ProviderAttemptError there is no
                    # evidence that the network boundary was reached.
                    last_error = error
                    split_on_failure = False
                    break
                except Exception as error:  # noqa: BLE001 - resumable batch failure handling.
                    # Generic exceptions may come from text protection, prompt
                    # construction, serialization, or local validation. Only a
                    # structured ProviderAttemptError may assert that a network
                    # attempt actually started.
                    last_error = error
                    split_on_failure = False
                    break
                else:
                    network_started = attempt_result.network_attempt_started
                    if network_started:
                        mark_api_attempt_done()
                    try:
                        apply_success(
                            batch,
                            translations,
                            started_at=started_at,
                            batch_index=batch_index,
                        )
                    except Exception:
                        if network_started:
                            record_usage_attempt(
                                ledger=usage_ledger,
                                translator=translator,
                                task_id=task_id,
                                batch_id=usage_batch_id,
                                attempt_number=attempt + 1,
                                batch=batch,
                                started_at=attempt_started_at,
                                latency_ms=elapsed_ms(attempt_started_perf),
                                outcome=UsageOutcome.SUCCESS,
                                retryable=False,
                                stable_error_code="",
                                attempt_result=attempt_result,
                                pricing_profiles=pricing_profiles,
                                write_failure_callback=lambda: emit_usage_diagnostic(
                                    "usage_ledger_write_failed",
                                    "用量账本保存失败，账本可能不完整；已完成的翻译结果仍会保存在本地。",
                                ),
                            )
                        raise
                    if network_started:
                        record_usage_attempt(
                            ledger=usage_ledger,
                            translator=translator,
                            task_id=task_id,
                            batch_id=usage_batch_id,
                            attempt_number=attempt + 1,
                            batch=batch,
                            started_at=attempt_started_at,
                            latency_ms=elapsed_ms(attempt_started_perf),
                            outcome=UsageOutcome.SUCCESS,
                            retryable=False,
                            stable_error_code="",
                            attempt_result=attempt_result,
                            pricing_profiles=pricing_profiles,
                            write_failure_callback=lambda: emit_usage_diagnostic(
                                "usage_ledger_write_failed",
                                "翻译结果已保存，但用量记录保存失败；账本可能不完整。",
                            ),
                        )
                    return

            if split_on_failure and config.retry_split_on_failure and len(batch) > 1:
                midpoint = max(1, len(batch) // 2)
                process_batch(batch[:midpoint], planned=False)
                process_batch(batch[midpoint:], planned=False)
                return

            if not continue_on_error:
                raise RuntimeError(str(last_error) if last_error else "batch failed")
            apply_failure(batch, last_error or RuntimeError("batch failed"), started_at=started_at, batch_index=batch_index)

        def apply_success(
            batch: list[PendingGroup],
            translations: list[str],
            *,
            started_at: float,
            batch_index: int,
        ) -> None:
            nonlocal translated_count, cache_hits, completed_rows, translated_rows
            row_count = sum(group.row_count for group in batch)
            with update_lock:
                for group, translation in zip(batch, translations, strict=True):
                    representative = group.representative
                    cache.set(
                        provider=translator.provider_name,
                        model=translator.model,
                        original=representative.original,
                        translation=translation,
                    )
                    for index, record in group.rows:
                        clear_review = bool(
                            force_ids is not None
                            and record.id in force_ids
                        )
                        updated[index] = replace(
                            record,
                            translation=translation,
                            note="",
                            review_status=(
                                "" if clear_review else record.review_status
                            ),
                            skip_status=(
                                "" if clear_review else record.skip_status
                            ),
                        )
                        emit_event(
                            event_callback,
                            TranslationItemCompleted(
                                text_id=record.id,
                                original=record.original,
                                translation=translation,
                                status="已翻译",
                                file_path=record.file_path,
                                source_type=record.source_type,
                                batch_index=batch_index,
                            ),
                        )
                        if sqlite_cache:
                            sqlite_cache.set(
                                record,
                                translation=translation,
                                provider=translator.provider_name,
                                model=translator.model,
                            )
                write_extracted_csv(updated, output_csv)
                for group, translation in zip(
                    batch,
                    translations,
                    strict=True,
                ):
                    for _index, record in group.rows:
                        record_provenance(
                            record,
                            translation,
                            TranslationSource.AI,
                        )

            elapsed = max(0.001, perf_counter() - started_at)
            with progress_lock:
                for group in batch:
                    translated_count += group.row_count
                    translated_rows += group.row_count
                    cache_hits += max(0, group.row_count - 1)
                    completed_rows += group.row_count
                recent_batch_rates.append(row_count / elapsed)
            emit_event(
                event_callback,
                TranslationBatchCompleted(
                    batch_index=batch_index,
                    api_time=elapsed,
                    translated_count=row_count,
                    failed_count=0,
                    check_result="通过",
                    cached_written=True,
                ),
            )
            emit_locked("translated batch")
            update_usage_task_stats()

        def apply_failure(
            batch: list[PendingGroup],
            error: Exception,
            *,
            started_at: float,
            batch_index: int,
        ) -> None:
            nonlocal failed_rows, completed_rows
            row_count = sum(group.row_count for group in batch)
            with update_lock:
                for group in batch:
                    for index, record in group.rows:
                        updated[index] = replace(record, note=f"failed: {error}")
                        emit_event(
                            event_callback,
                            TranslationItemFailed(
                                text_id=record.id,
                                original=record.original,
                                error=str(error),
                                file_path=record.file_path,
                                source_type=record.source_type,
                                batch_index=batch_index,
                            ),
                        )
                        if sqlite_cache:
                            sqlite_cache.set(
                                record,
                                translation="",
                                provider=translator.provider_name,
                                model=translator.model,
                                status="failed",
                                error=str(error),
                            )
                write_extracted_csv(updated, output_csv)

            elapsed = max(0.001, perf_counter() - started_at)
            with progress_lock:
                failed_rows += row_count
                completed_rows += row_count
                recent_batch_rates.append(row_count / elapsed)
            emit_event(
                event_callback,
                TranslationBatchCompleted(
                    batch_index=batch_index,
                    api_time=elapsed,
                    translated_count=0,
                    failed_count=row_count,
                    check_result=str(error),
                    cached_written=sqlite_cache is not None,
                ),
            )
            emit_locked("batch failed")
            update_usage_task_stats()

        emit_locked("loaded cache")

        if worker_count == 1 or len(batches) <= 1:
            for index, batch in enumerate(batches, start=1):
                if stop_event and stop_event.is_set():
                    break
                process_batch(batch, batch_index=index)
        else:
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                futures = [
                    executor.submit(process_batch, batch, batch_index=index)
                    for index, batch in enumerate(batches, start=1)
                ]
                for future in as_completed(futures):
                    if stop_event and stop_event.is_set():
                        for queued in futures:
                            queued.cancel()
                    future.result()
    finally:
        if sqlite_cache:
            sqlite_cache.close()
        if provenance_store:
            provenance_store.close()
        if owns_usage_ledger and usage_ledger:
            usage_ledger.close()

    write_extracted_csv(updated, output_csv)
    return updated, translated_count, cache_hits


def translate_batch_with_usage(
    translator: Translator,
    segments: list[TranslationSegment],
) -> ProviderAttemptResult:
    method = getattr(translator, "translate_batch_with_usage", None)
    if callable(method):
        result = method(segments)
        if not isinstance(result, ProviderAttemptResult):
            raise ProviderAttemptError(
                outcome=UsageOutcome.INVALID_RESPONSE,
                stable_error_code="invalid_provider_attempt_result",
                retryable=False,
                network_attempt_started=False,
            )
        return result
    return ProviderAttemptResult(
        translations=tuple(translator.translate_batch(segments)),
        usage=UsageNormalizationResult(
            tokens=TokenUsage(),
            diagnostics=("usage_unavailable_for_provider",),
        ),
    )


def record_usage_attempt(
    *,
    ledger: UsageLedger | None,
    translator: Translator,
    task_id: str,
    batch_id: str,
    attempt_number: int,
    batch: list[PendingGroup],
    started_at: str,
    latency_ms: int,
    outcome: UsageOutcome,
    retryable: bool,
    stable_error_code: str,
    attempt_result: ProviderAttemptResult | None,
    pricing_profiles: tuple[PricingProfile, ...],
    write_failure_callback: Callable[[], None] | None = None,
) -> bool:
    if ledger is None:
        return False
    metadata = attempt_result or ProviderAttemptResult(
        translations=(),
        usage=UsageNormalizationResult(
            tokens=TokenUsage(),
            diagnostics=("usage_unavailable_for_failed_attempt",),
        ),
    )
    provider = safe_usage_label(
        str(getattr(translator, "provider_name", "unknown")),
        fallback="unknown",
        collapse_custom_provider=True,
    )
    model = safe_usage_label(
        str(getattr(translator, "model", "unknown")),
        fallback="unknown",
        allow_slash=True,
    )
    endpoint_type = safe_usage_label(
        str(getattr(translator, "endpoint_type", "translation_batch")),
        fallback="translation_batch",
    )
    thinking_mode = safe_usage_label(
        str(getattr(translator, "thinking_mode", "")),
        fallback="",
    )
    profile = select_pricing_profile(
        pricing_profiles,
        provider=provider,
        model=model,
        request_started_at=started_at,
    )
    cost = estimate_cost(metadata.usage.tokens, profile)
    reported_cost = metadata.usage.provider_reported_cost
    currency = metadata.usage.currency
    if reported_cost is None and cost.amount is not None:
        currency = cost.currency
    elif reported_cost is not None and cost.amount is not None:
        if currency != cost.currency:
            cost = estimate_cost(metadata.usage.tokens, None)
    category_counts: dict[object, int] = {}
    source_types: set[str] = set()
    item_count = 0
    for group in batch:
        for _index, record in group.rows:
            category_id = category_for_record(record)
            category_counts[category_id] = category_counts.get(category_id, 0) + 1
            source_types.add(
                safe_source_type(record.source_type)
            )
            item_count += 1
    event_id = sha256(
        f"{task_id}\0{batch_id}\0{attempt_number}".encode("utf-8")
    ).hexdigest()
    event = ApiAttemptUsage(
            event_id=event_id,
            task_id=safe_usage_label(task_id, fallback="task"),
            batch_id=safe_usage_label(batch_id, fallback="batch"),
            attempt_number=attempt_number,
            provider=provider,
            model=model,
            endpoint_type=endpoint_type,
            thinking_mode=thinking_mode,
            category_items=tuple(
                UsageCategoryCount(category_id, count)
                for category_id, count in category_counts.items()
            ),
            source_types=tuple(source_types),
            item_count=item_count,
            tokens=metadata.usage.tokens,
            request_started_at=started_at,
            latency_ms=latency_ms,
            outcome=outcome,
            retryable=retryable,
            stable_error_code=safe_usage_label(
                stable_error_code,
                fallback="",
            ),
            provider_request_id=sanitize_provider_request_id(
                metadata.provider_request_id
            ),
            provider_reported_cost=reported_cost,
            estimated_cost=cost.amount,
            currency=currency,
            pricing_profile_id=cost.pricing_profile_id,
            usage_diagnostics=metadata.usage.diagnostics,
        )
    try:
        return ledger.record_attempt(event)
    except (OSError, sqlite3.Error):
        if write_failure_callback is not None:
            write_failure_callback()
        return False


def infer_usage_ledger_path(
    *,
    input_csv: Path,
    output_csv: Path,
    sqlite_cache_path: Path | None,
) -> Path:
    if sqlite_cache_path is not None:
        return Path(sqlite_cache_path).parent / "usage.sqlite3"
    for candidate in (Path(output_csv), Path(input_csv)):
        if candidate.parent.name == ".mc-han":
            return candidate.parent / "usage.sqlite3"
    return Path(output_csv).parent / ".mc-han" / "usage.sqlite3"


def safe_usage_label(
    value: str,
    *,
    fallback: str,
    collapse_custom_provider: bool = False,
    allow_slash: bool = False,
) -> str:
    value = value.strip()
    if collapse_custom_provider and value.casefold().startswith("custom:"):
        return "custom"
    if not value:
        return fallback
    if (
        "\\" in value
        or value.startswith("/")
        or (len(value) >= 3 and value[1:3] in {":/", ":\\"})
        or any(ord(character) < 32 for character in value)
    ):
        return fallback
    allowed = set(
        "abcdefghijklmnopqrstuvwxyz"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "0123456789._:@+-"
    )
    if allow_slash:
        allowed.add("/")
    normalized = "".join(character if character in allowed else "-" for character in value)
    normalized = normalized.strip("-")[:255]
    if allow_slash and any(part in {".", ".."} for part in normalized.split("/")):
        return fallback
    return normalized or fallback


def stable_error_code(error: Exception) -> str:
    if isinstance(error, TimeoutError):
        return "request_timeout"
    return {
        "ConnectionError": "network_error",
        "CancelledError": "request_cancelled",
    }.get(type(error).__name__, "provider_exception")


def safe_source_type(value: str) -> str:
    value = value.strip()
    if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value):
        return value
    return "unknown"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def elapsed_ms(started_at: float) -> int:
    return max(0, int(round((perf_counter() - started_at) * 1000)))


def batch_count(item_count: int, batch_size: int) -> int:
    if item_count <= 0:
        return 0
    return (item_count + max(1, batch_size) - 1) // max(1, batch_size)


def segment_from_group(group: PendingGroup) -> TranslationSegment:
    representative = group.representative
    return TranslationSegment(
        id=representative.id,
        text=representative.original,
        source_type=representative.source_type,
        file_path=representative.file_path,
        key_path=representative.key_path,
    )


def validate_batch_translations(segments: list[TranslationSegment], translations: list[str]) -> None:
    if len(translations) != len(segments):
        raise TranslationValidationError("Translator returned a different number of translations")
    for segment, translation in zip(segments, translations, strict=True):
        validate_translation(segment, translation)


def normalize_batch_translations(
    segments: list[TranslationSegment],
    translations: list[str],
    *,
    name_translation_format: str,
) -> list[str]:
    if len(translations) != len(segments):
        return translations
    normalized: list[str] = []
    for segment, translation in zip(segments, translations, strict=True):
        if is_name_source(segment.source_type):
            normalized.append(
                normalize_name_translation(
                    segment.text,
                    translation,
                    name_translation_format=name_translation_format,
                )
            )
        else:
            normalized.append(translation)
    return normalized


def validate_translation(segment: TranslationSegment, translation: str) -> None:
    if not str(translation).strip():
        raise TranslationValidationError(f"Translator returned an empty translation for {segment.id}")
    if not placeholders_match(segment.text, translation):
        raise TranslationValidationError(f"Placeholder/tag/color-code mismatch for {segment.id}")
    if extract_resource_ids(segment.text) != extract_resource_ids(translation):
        raise TranslationValidationError(f"Resource ID mismatch for {segment.id}")
    if fenced_code_blocks_closed(segment.text) and not fenced_code_blocks_closed(translation):
        raise TranslationValidationError(f"Markdown code fence mismatch for {segment.id}")
    if is_name_source(segment.source_type) and not name_translation_keeps_english(segment.text, translation):
        raise TranslationValidationError(f"Name translation must keep English original for {segment.id}")


def emit_checking_events(
    callback: Callable[[object], None] | None,
    segments: list[TranslationSegment],
    *,
    batch_index: int,
) -> None:
    for segment in segments:
        emit_event(
            callback,
            TranslationStarted(
                text_id=segment.id,
                original=segment.text,
                file_path=segment.file_path,
                source_type=segment.source_type,
                status="检查中",
                batch_index=batch_index,
            ),
        )


def emit_event(callback: Callable[[object], None] | None, event: object) -> None:
    if callback:
        callback(event)


def wait_until_resumed(pause_event: Event | None, stop_event: Event | None) -> bool:
    while pause_event and pause_event.is_set():
        if stop_event and stop_event.is_set():
            return False
        sleep(0.2)
    return not (stop_event and stop_event.is_set())


def wait_before_retry(seconds: float, pause_event: Event | None, stop_event: Event | None) -> bool:
    deadline = perf_counter() + seconds
    while perf_counter() < deadline:
        if not wait_until_resumed(pause_event, stop_event):
            return False
        if stop_event and stop_event.is_set():
            return False
        sleep(min(0.2, max(0.0, deadline - perf_counter())))
    return not (stop_event and stop_event.is_set())


def remaining_rows(total_rows: int, completed_rows: int) -> int:
    return max(0, total_rows - completed_rows)


def estimate_eta_seconds(*, remaining_rows: int, recent_batch_rates: deque[float]) -> float | None:
    if remaining_rows <= 0 or not recent_batch_rates:
        return None
    average_rate = sum(recent_batch_rates) / len(recent_batch_rates)
    if average_rate <= 0:
        return None
    return remaining_rows / average_rate


def emit_progress(
    callback: Callable[[TranslationProgress], None] | None,
    *,
    completed_rows: int,
    total_rows: int,
    translated_rows: int,
    cache_hits: int,
    failed_rows: int,
    api_translated_rows: int,
    remaining_rows: int,
    api_batches_done: int,
    api_batches_total: int,
    eta_seconds: float | None,
    message: str,
) -> None:
    if not callback:
        return
    callback(
        TranslationProgress(
            completed_rows=completed_rows,
            total_rows=total_rows,
            translated_rows=translated_rows,
            cache_hits=cache_hits,
            api_translated_rows=api_translated_rows,
            failed_rows=failed_rows,
            remaining_rows=remaining_rows,
            api_batches_done=api_batches_done,
            api_batches_total=api_batches_total,
            eta_seconds=eta_seconds,
            message=message,
        )
    )
