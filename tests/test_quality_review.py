from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from mc_han.csv_store import write_extracted_csv
from mc_han.models import ExtractedText
from mc_han.quality.checks import check_csv, check_output_dir
from mc_han.qt.translation_review_view_models import (
    ReviewFilterId,
    TranslationReviewPageViewModel,
)


def _record(
    record_id: str,
    *,
    original: str,
    translation: str,
    source_type: str = "jar_lang",
    file_path: str = "assets/demo/lang/en_us.json",
    key_path: str | None = None,
    review_status: str = "",
    skip_status: str = "",
) -> ExtractedText:
    return ExtractedText(
        id=record_id,
        source_type=source_type,
        container="mods/demo.jar",
        file_path=file_path,
        key_path=key_path or f"demo.{record_id}",
        original=original,
        translation=translation,
        review_status=review_status,
        skip_status=skip_status,
    )


def _check(tmp_path: Path, records: list[ExtractedText]):
    csv_path = tmp_path / "records.csv"
    write_extracted_csv(records, csv_path)
    return check_csv(csv_path)


def test_local_quality_rules_find_spacing_punctuation_and_length(
    tmp_path: Path,
):
    records = [
        _record(
            "spacing",
            original="Use the machine to generate power safely.",
            translation="使用 机器， 并保持安全。",
        ),
        _record(
            "punctuation",
            original="Open the terminal and inspect every storage cell.",
            translation="打开终端, 然后检查所有存储元件。",
        ),
        _record(
            "length",
            original="This detailed guide explains the complete machine setup.",
            translation="短",
        ),
    ]

    codes = {issue.code for issue in _check(tmp_path, records)}

    assert "chinese_spacing" in codes
    assert "chinese_punctuation" in codes
    assert "abnormal_translation_length" in codes


def test_cross_record_checks_find_terms_names_and_task_tone(
    tmp_path: Path,
):
    records = [
        _record(
            "term-a",
            original="Power Cell",
            translation="能源单元",
        ),
        _record(
            "term-b",
            original="Power Cell",
            translation="电力元件",
            file_path="assets/other/lang/en_us.json",
        ),
        _record(
            "name",
            original="Quantum Computer",
            translation="量子计算机 (Quantum Computer)",
            source_type="lang_name",
        ),
        _record(
            "body",
            original="Use the Quantum Computer to continue.",
            translation="使用量子电脑继续。",
            source_type="jar_ae2guide",
        ),
        _record(
            "tone-you",
            original="Open the quest.",
            translation="你需要打开任务。",
            source_type="ftbquests_snbt",
            file_path="config/ftbquests/quests/chapter.snbt",
        ),
        _record(
            "tone-formal",
            original="Claim the reward.",
            translation="您可以领取奖励。",
            source_type="ftbquests_snbt",
            file_path="config/ftbquests/quests/chapter.snbt",
        ),
    ]

    codes = {issue.code for issue in _check(tmp_path, records)}

    assert "term_translation_inconsistent" in codes
    assert "name_body_inconsistent" in codes
    assert "task_tone_inconsistent" in codes


def test_skipped_records_do_not_create_missing_translation_warning(
    tmp_path: Path,
):
    issues = _check(
        tmp_path,
        [
            _record(
                "skipped",
                original="Untranslated text",
                translation="",
                skip_status="user_skipped",
            )
        ],
    )

    assert issues == []


def test_review_summary_defaults_to_actionable_anomalies_and_shows_cost(
    tmp_path: Path,
):
    records = (
        _record(
            "passed",
            original="Open the terminal %s",
            translation="打开终端 %s",
        ),
        _record(
            "warning",
            original="Use the machine to generate power safely.",
            translation="使用 机器安全发电。",
        ),
        _record(
            "confirmed",
            original="Use another machine to generate power safely.",
            translation="使用 另一台机器安全发电。",
            review_status="approved",
        ),
        _record(
            "missing",
            original="Missing translation",
            translation="",
        ),
        _record(
            "skipped",
            original="Skipped translation",
            translation="",
            skip_status="user_skipped",
        ),
    )
    issues = tuple(_check(tmp_path, list(records)))
    view_model = TranslationReviewPageViewModel.from_data(
        records,
        issues,
        cost_amount=Decimal("0.1234"),
        cost_currency="USD",
    )

    issue_ids = {
        row.text_id
        for row in view_model.filtered_rows("", ReviewFilterId.ISSUES)
    }
    assert issue_ids == {"warning", "missing"}
    assert view_model.passed_count == 1
    assert view_model.needs_confirmation_count == 1
    assert view_model.unresolved_count == 1
    assert view_model.skipped_count == 1
    assert view_model.cost_text == "USD 0.1234"


def test_output_quality_check_rejects_structurally_broken_snbt(
    tmp_path: Path,
):
    output = tmp_path / "output"
    output.mkdir()
    (output / "valid.snbt").write_text(
        '{title:"括号 ] 在字符串中", quests:[]}',
        encoding="utf-8",
    )
    (output / "broken.snbt").write_text(
        '{title:"未闭合", quests:[]',
        encoding="utf-8",
    )

    issues = check_output_dir(output)

    assert [
        (issue.code, issue.location)
        for issue in issues
        if issue.code == "invalid_snbt"
    ] == [("invalid_snbt", "broken.snbt")]
