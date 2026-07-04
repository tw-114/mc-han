from __future__ import annotations

from collections import Counter
from pathlib import Path

from .csv_store import read_extracted_csv
from .models import ExtractedText


def build_preview(
    csv_path: Path,
    *,
    limit: int = 20,
    translated_only: bool = False,
    untranslated_only: bool = False,
) -> str:
    records = read_extracted_csv(csv_path)
    filtered = filter_records(
        records,
        translated_only=translated_only,
        untranslated_only=untranslated_only,
    )
    source_counts = Counter(record.source_type for record in records)
    translated_count = sum(1 for record in records if record.translation.strip())
    lines = [
        "mc-han preview",
        f"csv: {Path(csv_path)}",
        f"rows: {len(records)}",
        f"translated_rows: {translated_count}",
        "source_counts:",
    ]
    for source_type, count in source_counts.most_common():
        lines.append(f"  {source_type}: {count}")
    lines.extend(["", f"sample_rows: {min(limit, len(filtered))}"])
    for index, record in enumerate(filtered[:limit], start=1):
        lines.append(f"{index}. {record.source_type} :: {record.container} :: {record.file_path}")
        lines.append(f"   key: {record.key_path}")
        lines.append(f"   original: {single_line(record.original)}")
        if record.translation:
            lines.append(f"   translation: {single_line(record.translation)}")
    return "\n".join(lines) + "\n"


def filter_records(
    records: list[ExtractedText],
    *,
    translated_only: bool,
    untranslated_only: bool,
) -> list[ExtractedText]:
    if translated_only and untranslated_only:
        return records
    if translated_only:
        return [record for record in records if record.translation.strip()]
    if untranslated_only:
        return [record for record in records if not record.translation.strip()]
    return records


def single_line(value: str, max_length: int = 180) -> str:
    text = " ".join(value.split())
    if len(text) <= max_length:
        return text
    return text[: max_length - 3] + "..."
