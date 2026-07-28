from __future__ import annotations

from dataclasses import replace
from enum import Enum
from pathlib import Path

from mc_han.csv_store import read_extracted_csv, write_extracted_csv
from mc_han.models import ExtractedText
from mc_han.quality.checks import CheckIssue, check_csv


class ReviewAction(str, Enum):
    EDIT = "edit"
    APPROVE = "approve"
    NEEDS_RETRANSLATE = "needs_retranslate"
    SKIP = "skip"


def load_translation_review(
    csv_path: Path,
) -> tuple[tuple[ExtractedText, ...], tuple[CheckIssue, ...]]:
    records = tuple(read_extracted_csv(csv_path))
    issues = tuple(check_csv(csv_path))
    return records, issues


def update_translation_review_record(
    csv_path: Path,
    record_id: str,
    action: ReviewAction,
    *,
    translation: str = "",
) -> tuple[ExtractedText, ...]:
    if not isinstance(action, ReviewAction):
        raise TypeError("action must be ReviewAction")
    if not isinstance(record_id, str) or not record_id:
        raise ValueError("record_id must not be empty")
    if not isinstance(translation, str):
        raise TypeError("translation must be a string")

    records = read_extracted_csv(csv_path)
    updated: list[ExtractedText] = []
    matched = False
    for record in records:
        if record.id != record_id:
            updated.append(record)
            continue
        matched = True
        updated.append(_apply_action(record, action, translation))
    if not matched:
        raise ValueError("review record was not found")

    # The shared writer performs same-directory temporary writes and os.replace.
    write_extracted_csv(updated, csv_path)
    return tuple(updated)


def _apply_action(
    record: ExtractedText,
    action: ReviewAction,
    translation: str,
) -> ExtractedText:
    if action is ReviewAction.EDIT:
        return replace(
            record,
            translation=translation,
            note="edited",
            review_status="",
            skip_status="",
        )
    if action is ReviewAction.APPROVE:
        if not record.translation.strip():
            raise ValueError("translation must not be empty before approval")
        return replace(
            record,
            review_status="approved",
            skip_status="",
        )
    if action is ReviewAction.NEEDS_RETRANSLATE:
        return replace(
            record,
            translation="",
            note="needs_retranslate",
            review_status="needs_retranslate",
            skip_status="",
        )
    return replace(
        record,
        review_status="",
        skip_status="user_skipped",
    )
