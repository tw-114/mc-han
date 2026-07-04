from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Event, Lock
from time import perf_counter, sleep
from typing import Callable

from mc_han.csv_store import read_extracted_csv, write_extracted_csv
from mc_han.config import DEFAULT_NAME_TRANSLATION_FORMAT
from mc_han.models import ExtractedText
from mc_han.quality.checks import extract_resource_ids
from mc_han.quality.markdown import fenced_code_blocks_closed
from mc_han.quality.placeholders import placeholders_match

from .base import TranslationSegment, Translator
from .batching import PendingGroup, build_token_batches, make_pending_group, resolve_speed_mode
from .cache import TranslationCache, make_reuse_key
from .names import is_name_source, name_translation_keeps_english, normalize_name_translation
from .sqlite_cache import SQLiteTranslationCache


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

    update_lock = Lock()
    progress_lock = Lock()
    recent_batch_rates: deque[float] = deque(maxlen=10)

    translated_count = 0
    cache_hits = 0
    failed_rows = 0
    already_translated_rows = 0
    completed_rows = 0
    translated_rows = 0
    api_batches_done = 0
    api_batches_total = 0

    pending_order: list[str] = []
    pending_groups: dict[str, list[tuple[int, ExtractedText]]] = {}

    try:
        for index, record in enumerate(records):
            if not record.original.strip():
                continue
            if target_ids is not None and record.id not in target_ids:
                continue
            if record.translation and not force:
                already_translated_rows += 1
                continue

            cached = sqlite_cache.get(record) if sqlite_cache and not force else None
            if cached is None:
                cached = cache.get(
                    provider=translator.provider_name,
                    model=translator.model,
                    original=record.original,
                )
            if cached is not None and not force:
                updated[index] = replace(record, translation=cached, note="")
                if sqlite_cache:
                    sqlite_cache.set(
                        record,
                        translation=cached,
                        provider=translator.provider_name,
                        model=translator.model,
                    )
                cache_hits += 1
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
            if not batch:
                return
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
                try:
                    translations = translator.translate_batch(segments)
                    emit_checking_events(event_callback, segments, batch_index=batch_index)
                    translations = normalize_batch_translations(
                        segments,
                        translations,
                        name_translation_format=name_translation_format,
                    )
                    validate_batch_translations(segments, translations)
                    mark_api_attempt_done()
                    apply_success(batch, translations, started_at=started_at, batch_index=batch_index)
                    return
                except TranslationValidationError as error:
                    mark_api_attempt_done()
                    last_error = error
                    break
                except Exception as error:  # noqa: BLE001 - resumable batch failure handling.
                    mark_api_attempt_done()
                    last_error = error

            if config.retry_split_on_failure and len(batch) > 1:
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
                        updated[index] = replace(record, translation=translation, note="")
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

    write_extracted_csv(updated, output_csv)
    return updated, translated_count, cache_hits


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
