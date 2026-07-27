from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha1


@dataclass(frozen=True)
class ExtractedText:
    id: str
    source_type: str
    container: str
    file_path: str
    key_path: str
    original: str
    translation: str = ""
    note: str = ""
    review_status: str = ""
    skip_status: str = ""

    def to_csv_row(self) -> dict[str, str]:
        return {
            "id": self.id,
            "source_type": self.source_type,
            "container": self.container,
            "file_path": self.file_path,
            "key_path": self.key_path,
            "original": self.original,
            "translation": self.translation,
            "note": self.note,
            "review_status": self.review_status,
            "skip_status": self.skip_status,
        }


CSV_FIELDS = (
    "id",
    "source_type",
    "container",
    "file_path",
    "key_path",
    "original",
    "translation",
    "note",
    "review_status",
    "skip_status",
)


def make_record(
    *,
    source_type: str,
    container: str,
    file_path: str,
    key_path: str,
    original: str,
    note: str = "",
) -> ExtractedText:
    stable = "\0".join((source_type, container, file_path, key_path, original))
    record_id = sha1(stable.encode("utf-8")).hexdigest()[:16]
    return ExtractedText(
        id=record_id,
        source_type=source_type,
        container=container,
        file_path=file_path,
        key_path=key_path,
        original=original,
        note=note,
    )
