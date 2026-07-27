from __future__ import annotations

import csv
import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .models import CSV_FIELDS, ExtractedText


class UnsupportedCsvSchemaError(ValueError):
    """Raised when rewriting a CSV would discard non-empty unknown columns."""


@dataclass(frozen=True)
class CsvWriteError(OSError):
    phase: str
    original_preserved: bool
    target_previously_existed: bool

    def __str__(self) -> str:
        return f"atomic CSV write failed during {self.phase}"


@dataclass(frozen=True)
class _FileSnapshot:
    existed: bool
    size: int = 0
    digest: str = ""


def read_extracted_csv(path: Path) -> list[ExtractedText]:
    with Path(path).open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        rows = list(reader)
    unknown_fields = tuple(
        field
        for field in (reader.fieldnames or ())
        if field and field not in CSV_FIELDS
    )
    nonempty_unknown = tuple(
        field
        for field in unknown_fields
        if any((row.get(field) or "").strip() for row in rows)
    )
    if nonempty_unknown:
        names = ", ".join(sorted(set(nonempty_unknown)))
        raise UnsupportedCsvSchemaError(
            f"CSV contains unsupported non-empty column(s): {names}"
        )

    records: list[ExtractedText] = []
    for row in rows:
        records.append(
            ExtractedText(
                id=row.get("id", ""),
                source_type=row.get("source_type", ""),
                container=row.get("container", ""),
                file_path=row.get("file_path", ""),
                key_path=row.get("key_path", ""),
                original=row.get("original", ""),
                translation=row.get("translation", ""),
                note=row.get("note", ""),
                review_status=row.get("review_status", ""),
                skip_status=row.get("skip_status", ""),
            )
        )
    return records


def write_extracted_csv(records: list[ExtractedText], output_path: Path) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    before = _snapshot_file(output_path)
    temporary_path: Path | None = None
    descriptor: int | None = None
    phase = "temporary_file"
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".mc-han-csv-",
            suffix=".tmp",
            dir=output_path.parent,
        )
        temporary_path = Path(temporary_name)
        phase = "write"
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as file:
            descriptor = None
            writer = csv.DictWriter(file, fieldnames=CSV_FIELDS)
            writer.writeheader()
            for record in records:
                writer.writerow(record.to_csv_row())
            phase = "flush"
            file.flush()
            phase = "fsync"
            fsync = getattr(os, "fsync", None)
            if fsync is not None:
                fsync(file.fileno())
            phase = "close"
        phase = "replace"
        os.replace(temporary_path, output_path)
        temporary_path = None
    except Exception as error:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        preserved = _matches_snapshot(output_path, before)
        raise CsvWriteError(
            phase=phase,
            original_preserved=preserved,
            target_previously_existed=before.existed,
        ) from error


def _snapshot_file(path: Path) -> _FileSnapshot:
    if not path.exists():
        return _FileSnapshot(existed=False)
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return _FileSnapshot(existed=True, size=size, digest=digest.hexdigest())


def _matches_snapshot(path: Path, snapshot: _FileSnapshot) -> bool:
    try:
        current = _snapshot_file(path)
    except OSError:
        return False
    return current == snapshot
