from __future__ import annotations

import csv
from pathlib import Path

from .models import CSV_FIELDS, ExtractedText


def read_extracted_csv(path: Path) -> list[ExtractedText]:
    with Path(path).open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
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
            )
        )
    return records


def write_extracted_csv(records: list[ExtractedText], output_path: Path) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for record in records:
            writer.writerow(record.to_csv_row())
