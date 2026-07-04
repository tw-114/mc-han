from __future__ import annotations

from pathlib import Path
from threading import Lock

from mc_han.csv_store import read_extracted_csv, write_extracted_csv
from mc_han.models import ExtractedText
from mc_han.translator.base import TranslationSegment, Translator
from mc_han.translator.batching import build_token_batches, make_pending_group, resolve_speed_mode
from mc_han.translator.engine import TranslationProgress, translate_csv


class BatchRecordingTranslator(Translator):
    provider_name = "deepseek"
    model = "deepseek-v4"

    def __init__(self) -> None:
        self.batch_sizes: list[int] = []
        self.segment_count = 0
        self._lock = Lock()

    def translate_batch(self, segments: list[TranslationSegment]) -> list[str]:
        with self._lock:
            self.batch_sizes.append(len(segments))
            self.segment_count += len(segments)
        return [safe_translation(segment) for segment in segments]


class SplitOnValidationTranslator(Translator):
    provider_name = "deepseek"
    model = "deepseek-v4"

    def __init__(self) -> None:
        self.batch_sizes: list[int] = []

    def translate_batch(self, segments: list[TranslationSegment]) -> list[str]:
        self.batch_sizes.append(len(segments))
        if len(segments) > 1:
            return ["破坏格式" for _segment in segments]
        return [safe_translation(segment) for segment in segments]


def test_speed_modes_batch_short_texts_by_token_budget():
    groups = [
        make_pending_group(
            f"key-{index}",
            [(index, make_record(str(index), f"Open the guide page number {index}."))],
        )
        for index in range(10827)
    ]

    safe_batches = build_token_batches(groups, resolve_speed_mode("safe"))
    balanced_batches = build_token_batches(groups, resolve_speed_mode("balanced"))
    fast_batches = build_token_batches(groups, resolve_speed_mode("fast"))

    assert all(len(batch) <= 20 for batch in safe_batches)
    assert all(len(batch) <= 60 for batch in balanced_batches)
    assert all(len(batch) <= 150 for batch in fast_batches)
    assert len(balanced_batches) < 250
    assert len(fast_batches) < len(balanced_batches) < len(safe_batches)


def test_long_text_is_placed_in_its_own_batch():
    short_before = make_pending_group("short-before", [(0, make_record("0", "Short tooltip."))])
    long_text = "\n".join(f"Long guide paragraph {index} with AE2 and ME storage details." for index in range(300))
    long_group = make_pending_group("long", [(1, make_record("1", long_text))])
    short_after = make_pending_group("short-after", [(2, make_record("2", "Another short tooltip."))])

    batches = build_token_batches([short_before, long_group, short_after], resolve_speed_mode("balanced"))

    assert [long_group] in batches


def test_fast_mode_splits_failed_batch_and_retries_smaller_groups(tmp_path: Path):
    csv_path = tmp_path / "extracted_texts.csv"
    output_csv = tmp_path / "translated.csv"
    write_extracted_csv(
        [make_record(str(index), f"Use %s to open guide page {index}.") for index in range(4)],
        csv_path,
    )
    translator = SplitOnValidationTranslator()

    records, translated_count, cache_hits = translate_csv(
        input_csv=csv_path,
        output_csv=output_csv,
        translator=translator,
        cache_path=tmp_path / "cache.jsonl",
        speed_mode="fast",
        continue_on_error=True,
        max_retries=0,
        retry_delay_seconds=0,
    )

    assert translator.batch_sizes[0] == 4
    assert 1 in translator.batch_sizes
    assert translated_count == 4
    assert cache_hits == 0
    assert all(record.translation for record in records)
    assert all("%s" in record.translation for record in read_extracted_csv(output_csv))


def test_concurrent_translation_writes_sqlite_and_resume_reuses_cache(tmp_path: Path):
    csv_path = tmp_path / "extracted_texts.csv"
    first_output = tmp_path / "first.csv"
    second_output = tmp_path / "second.csv"
    sqlite_path = tmp_path / ".mc-han" / "translations.sqlite"
    records = [make_record(str(index), f"Quest instruction {index}.") for index in range(90)]
    write_extracted_csv(records, csv_path)
    first_translator = BatchRecordingTranslator()

    translate_csv(
        input_csv=csv_path,
        output_csv=first_output,
        translator=first_translator,
        cache_path=tmp_path / "cache.jsonl",
        sqlite_cache_path=sqlite_path,
        speed_mode="balanced",
        worker_count=3,
        retry_delay_seconds=0,
    )
    write_extracted_csv(records, csv_path)
    second_translator = BatchRecordingTranslator()
    progress: list[TranslationProgress] = []

    resumed, translated_count, cache_hits = translate_csv(
        input_csv=csv_path,
        output_csv=second_output,
        translator=second_translator,
        cache_path=tmp_path / "empty-cache.jsonl",
        sqlite_cache_path=sqlite_path,
        speed_mode="balanced",
        worker_count=3,
        progress_callback=progress.append,
        retry_delay_seconds=0,
    )

    assert sqlite_path.exists()
    assert first_translator.segment_count == 90
    assert second_translator.segment_count == 0
    assert translated_count == 0
    assert cache_hits == 90
    assert all(record.translation for record in resumed)
    assert progress[-1].translated_rows == 90
    assert progress[-1].remaining_rows == 0


def make_record(identifier: str, original: str) -> ExtractedText:
    return ExtractedText(
        id=identifier,
        source_type="jar_guides",
        container="mods/demo.jar",
        file_path=f"assets/demo/guides/{identifier}.md",
        key_path=f"line.{identifier}",
        original=original,
    )


def safe_translation(segment: TranslationSegment) -> str:
    if "%s" in segment.text:
        return f"译文 %s {segment.id}"
    return f"译文 {segment.id}"
