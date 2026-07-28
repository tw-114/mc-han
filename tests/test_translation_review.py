from __future__ import annotations

from pathlib import Path

import pytest

from mc_han import csv_store
from mc_han.csv_store import CsvWriteError, read_extracted_csv, write_extracted_csv
from mc_han.models import ExtractedText
from mc_han.quality.checks import check_csv
from mc_han.qt.translation_review_view_models import (
    ReviewFilterId,
    ReviewRowStatus,
    TranslationReviewPageViewModel,
)
from mc_han.services.translation_review import (
    ReviewAction,
    load_translation_review,
    update_translation_review_record,
)


def _record(
    text_id: str,
    *,
    original: str | None = None,
    translation: str = "",
    note: str = "",
    review_status: str = "",
    skip_status: str = "",
    source_type: str = "jar_lang",
) -> ExtractedText:
    return ExtractedText(
        id=text_id,
        source_type=source_type,
        container=f"mods/{text_id}.jar",
        file_path=f"assets/{text_id}/lang/en_us.json",
        key_path=f"{text_id}.description",
        original=original or f"Original {text_id} %s",
        translation=translation,
        note=note,
        review_status=review_status,
        skip_status=skip_status,
    )


def _write(tmp_path: Path, records: list[ExtractedText]) -> Path:
    path = tmp_path / ".mc-han" / "extracted_texts.csv"
    write_extracted_csv(records, path)
    return path


def test_edit_uses_shared_atomic_csv_and_preserves_other_records(
    tmp_path: Path,
):
    target = _record(
        "target",
        translation="旧译文 %s",
        review_status="approved",
    )
    untouched = _record(
        "other",
        translation="保留译文 %s",
        note="review:ok",
        review_status="approved",
        skip_status="",
    )
    csv_path = _write(tmp_path, [target, untouched])

    update_translation_review_record(
        csv_path,
        "target",
        ReviewAction.EDIT,
        translation="人工编辑 %s",
    )

    saved = {record.id: record for record in read_extracted_csv(csv_path)}
    assert saved["target"].translation == "人工编辑 %s"
    assert saved["target"].note == "edited"
    assert saved["target"].review_status == ""
    assert saved["other"] == untouched


def test_review_actions_update_existing_status_columns(tmp_path: Path):
    csv_path = _write(
        tmp_path,
        [
            _record("approved", translation="译文 %s"),
            _record("retry", translation="待重译 %s"),
            _record("skipped", translation="保留但跳过 %s"),
        ],
    )

    update_translation_review_record(
        csv_path,
        "approved",
        ReviewAction.APPROVE,
    )
    update_translation_review_record(
        csv_path,
        "retry",
        ReviewAction.NEEDS_RETRANSLATE,
    )
    update_translation_review_record(
        csv_path,
        "skipped",
        ReviewAction.SKIP,
    )

    saved = {record.id: record for record in read_extracted_csv(csv_path)}
    assert saved["approved"].review_status == "approved"
    assert saved["retry"].translation == ""
    assert saved["retry"].note == "needs_retranslate"
    assert saved["retry"].review_status == "needs_retranslate"
    assert saved["skipped"].translation == "保留但跳过 %s"
    assert saved["skipped"].skip_status == "user_skipped"


def test_review_view_model_searches_filters_and_reuses_quality_issues(
    tmp_path: Path,
):
    csv_path = _write(
        tmp_path,
        [
            _record("missing"),
            _record("failed", note="failed: fake"),
            _record(
                "unreviewed",
                original="Energy guide %s",
                translation="能源指南 %s",
                source_type="jar_patchouli",
            ),
            _record(
                "reviewed",
                translation="已审核 %s",
                review_status="approved",
            ),
            _record(
                "skipped",
                translation="跳过 %s",
                skip_status="user_skipped",
            ),
            _record(
                "broken",
                original="Broken %s",
                translation="损坏",
            ),
        ],
    )
    records, issues = load_translation_review(csv_path)
    view_model = TranslationReviewPageViewModel.from_data(records, issues)

    assert [row.text_id for row in view_model.filtered_rows(
        "energy",
        ReviewFilterId.ALL,
    )] == ["unreviewed"]
    assert {
        row.text_id
        for row in view_model.filtered_rows(
            "",
            ReviewFilterId.UNTRANSLATED,
        )
    } == {"missing", "failed"}
    assert [row.text_id for row in view_model.filtered_rows(
        "",
        ReviewFilterId.FAILED,
    )] == ["failed"]
    assert [row.text_id for row in view_model.filtered_rows(
        "",
        ReviewFilterId.REVIEWED,
    )] == ["reviewed"]
    assert [row.text_id for row in view_model.filtered_rows(
        "",
        ReviewFilterId.SKIPPED,
    )] == ["skipped"]
    broken = next(row for row in view_model.rows if row.text_id == "broken")
    assert broken.status is ReviewRowStatus.UNREVIEWED
    assert broken.has_error
    assert any("placeholder_mismatch" in issue for issue in broken.issues)
    assert tuple(issues) == tuple(check_csv(csv_path))


def test_atomic_save_failure_preserves_old_csv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    csv_path = _write(
        tmp_path,
        [_record("target", translation="原有译文 %s")],
    )
    before = csv_path.read_bytes()

    def fail_replace(_source, _target):
        raise PermissionError("simulated")

    monkeypatch.setattr(csv_store.os, "replace", fail_replace)
    with pytest.raises(CsvWriteError) as caught:
        update_translation_review_record(
            csv_path,
            "target",
            ReviewAction.EDIT,
            translation="不能保存 %s",
        )

    assert caught.value.original_preserved
    assert csv_path.read_bytes() == before
    assert not list(csv_path.parent.glob(".mc-han-csv-*.tmp"))
