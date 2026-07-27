from __future__ import annotations

import csv
from pathlib import Path

import pytest

from mc_han import csv_store
from mc_han.csv_store import (
    CsvWriteError,
    UnsupportedCsvSchemaError,
    read_extracted_csv,
    write_extracted_csv,
)
from mc_han.models import ExtractedText


def sample_record(**overrides: str) -> ExtractedText:
    values = {
        "id": "demo",
        "source_type": "jar_lang",
        "container": "mods/demo.jar",
        "file_path": "assets/demo/lang/en_us.json",
        "key_path": "message.demo",
        "original": "Demo",
        "translation": "演示",
        "note": "edited",
        "review_status": "approved",
        "skip_status": "",
    }
    values.update(overrides)
    return ExtractedText(**values)


def temporary_csv_files(directory: Path) -> list[Path]:
    return list(directory.glob(".mc-han-csv-*.tmp"))


def install_failing_writer(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fail_after_rows: int,
) -> None:
    real_writer = csv_store.csv.DictWriter

    class FailingWriter:
        def __init__(self, *args, **kwargs) -> None:
            self._delegate = real_writer(*args, **kwargs)
            self._rows = 0

        def writeheader(self):
            return self._delegate.writeheader()

        def writerow(self, row):
            self._rows += 1
            if self._rows > fail_after_rows:
                raise OSError("simulated write failure")
            return self._delegate.writerow(row)

    monkeypatch.setattr(csv_store.csv, "DictWriter", FailingWriter)


def test_atomic_csv_write_round_trips_status_fields(tmp_path: Path):
    target = tmp_path / "extracted_texts.csv"
    expected = sample_record(skip_status="user_skipped")

    write_extracted_csv([expected], target)

    assert read_extracted_csv(target) == [expected]
    assert not temporary_csv_files(tmp_path)


def test_atomic_csv_mid_write_failure_preserves_old_bytes_and_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    target = tmp_path / "extracted_texts.csv"
    write_extracted_csv([sample_record()], target)
    before = target.read_bytes()
    install_failing_writer(monkeypatch, fail_after_rows=1)

    with pytest.raises(CsvWriteError) as captured:
        write_extracted_csv(
            [
                sample_record(id="one"),
                sample_record(id="two"),
            ],
            target,
        )

    assert captured.value.phase == "write"
    assert captured.value.original_preserved
    assert target.read_bytes() == before
    assert not temporary_csv_files(tmp_path)


def test_atomic_csv_replace_failure_preserves_old_bytes_and_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    target = tmp_path / "extracted_texts.csv"
    write_extracted_csv([sample_record()], target)
    before = target.read_bytes()

    def fail_replace(source: Path, destination: Path) -> None:
        raise PermissionError("simulated replace failure")

    monkeypatch.setattr(csv_store.os, "replace", fail_replace)

    with pytest.raises(CsvWriteError) as captured:
        write_extracted_csv([sample_record(translation="新译文")], target)

    assert captured.value.phase == "replace"
    assert captured.value.original_preserved
    assert target.read_bytes() == before
    assert not temporary_csv_files(tmp_path)


def test_atomic_csv_first_write_failure_leaves_no_target_or_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    target = tmp_path / "extracted_texts.csv"
    install_failing_writer(monkeypatch, fail_after_rows=0)

    with pytest.raises(CsvWriteError) as captured:
        write_extracted_csv([sample_record()], target)

    assert captured.value.phase == "write"
    assert captured.value.original_preserved
    assert not captured.value.target_previously_existed
    assert not target.exists()
    assert not temporary_csv_files(tmp_path)


def test_atomic_csv_fsync_failure_preserves_old_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    target = tmp_path / "extracted_texts.csv"
    write_extracted_csv([sample_record()], target)
    before = target.read_bytes()

    def fail_fsync(descriptor: int) -> None:
        raise OSError("simulated disk failure")

    monkeypatch.setattr(csv_store.os, "fsync", fail_fsync)

    with pytest.raises(CsvWriteError) as captured:
        write_extracted_csv([sample_record(translation="新译文")], target)

    assert captured.value.phase == "fsync"
    assert captured.value.original_preserved
    assert target.read_bytes() == before
    assert not temporary_csv_files(tmp_path)


@pytest.mark.parametrize("failure_stage", ("flush", "close"))
def test_atomic_csv_flush_or_close_failure_preserves_old_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
):
    target = tmp_path / "extracted_texts.csv"
    write_extracted_csv([sample_record()], target)
    before = target.read_bytes()
    real_fdopen = csv_store.os.fdopen

    class FailingFile:
        def __init__(self, descriptor: int, *args, **kwargs) -> None:
            self._file = real_fdopen(descriptor, *args, **kwargs)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            self._file.close()
            if exc_type is None and failure_stage == "close":
                raise OSError("simulated close failure")
            return False

        def __getattr__(self, name: str):
            return getattr(self._file, name)

        def flush(self) -> None:
            if failure_stage == "flush":
                raise OSError("simulated flush failure")
            self._file.flush()

    monkeypatch.setattr(csv_store.os, "fdopen", FailingFile)

    with pytest.raises(CsvWriteError) as captured:
        write_extracted_csv([sample_record(translation="新译文")], target)

    assert captured.value.phase == failure_stage
    assert captured.value.original_preserved
    assert target.read_bytes() == before
    assert not temporary_csv_files(tmp_path)


def test_old_eight_column_csv_remains_readable(tmp_path: Path):
    target = tmp_path / "legacy.csv"
    with target.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=(
                "id",
                "source_type",
                "container",
                "file_path",
                "key_path",
                "original",
                "translation",
                "note",
            ),
        )
        writer.writeheader()
        row = sample_record().to_csv_row()
        writer.writerow({field: row[field] for field in writer.fieldnames})

    record = read_extracted_csv(target)[0]

    assert record.translation == "演示"
    assert record.note == "edited"
    assert record.review_status == ""
    assert record.skip_status == ""


def test_unknown_nonempty_csv_column_is_rejected(tmp_path: Path):
    target = tmp_path / "future.csv"
    fields = [*sample_record().to_csv_row(), "future_status"]
    with target.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerow(
            {
                **sample_record().to_csv_row(),
                "future_status": "must-not-be-lost",
            }
        )

    before = target.read_bytes()
    with pytest.raises(UnsupportedCsvSchemaError, match="future_status"):
        read_extracted_csv(target)

    assert target.read_bytes() == before
