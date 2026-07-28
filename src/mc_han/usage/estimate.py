from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Collection, Iterable

from mc_han.models import ExtractedText
from mc_han.workflow.scan_models import ScanSelectionState


@dataclass(frozen=True)
class SelectedWorkEstimate:
    selected_record_count: int
    untranslated_record_count: int
    cached_record_count: int
    skipped_record_count: int
    estimated_input_characters: int
    estimated_output_characters: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected_record_count": self.selected_record_count,
            "untranslated_record_count": self.untranslated_record_count,
            "cached_record_count": self.cached_record_count,
            "skipped_record_count": self.skipped_record_count,
            "estimated_input_characters": self.estimated_input_characters,
            "estimated_output_characters": self.estimated_output_characters,
            "unit": "characters",
        }


def estimate_selected_work(
    records: Iterable[ExtractedText],
    selection: ScanSelectionState,
    *,
    cached_record_ids: Collection[str] = (),
) -> SelectedWorkEstimate:
    selected = selection.selected_records(records)
    cached_ids = frozenset(cached_record_ids)
    skipped = tuple(record for record in selected if _is_skipped(record))
    eligible = tuple(record for record in selected if not _is_skipped(record))
    cached = tuple(record for record in eligible if record.id in cached_ids)
    untranslated = tuple(
        record
        for record in eligible
        if not record.translation.strip() and record.id not in cached_ids
    )
    input_characters = sum(len(record.original) for record in untranslated)
    return SelectedWorkEstimate(
        selected_record_count=len(selected),
        untranslated_record_count=len(untranslated),
        cached_record_count=len(cached),
        skipped_record_count=len(skipped),
        estimated_input_characters=input_characters,
        estimated_output_characters=input_characters,
    )


def _is_skipped(record: ExtractedText) -> bool:
    if record.skip_status.strip():
        return True
    legacy_note = record.note.strip().casefold()
    return (
        legacy_note in {"skip", "skipped", "user_skipped"}
        or legacy_note.startswith(("skip:", "skip="))
    )
