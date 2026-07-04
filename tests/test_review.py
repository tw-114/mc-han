from __future__ import annotations

from mc_han.cli import run_review
from mc_han.csv_store import write_extracted_csv
from mc_han.models import ExtractedText
from mc_han.review import build_review_html, write_review_report


def create_review_csv(path):
    write_extracted_csv(
        [
            ExtractedText(
                id="1",
                source_type="jar_lang",
                container="mods/demo.jar",
                file_path="assets/demo/lang/en_us.json",
                key_path="message.demo",
                original="Use %s to start.",
                translation="使用 %s 开始。",
            ),
            ExtractedText(
                id="2",
                source_type="jar_patchouli",
                container="mods/demo.jar",
                file_path="assets/demo/patchouli_books/book/en_us/entries/intro.json",
                key_path="pages[0].text",
                original="Use $(item)Quartz$(0).",
                translation="使用石英。",
            ),
        ],
        path,
    )


def test_build_review_html_contains_stats_rows_and_issues(tmp_path):
    csv_path = tmp_path / "translated.csv"
    create_review_csv(csv_path)

    html, stats = build_review_html(csv_path=csv_path, limit=None)

    assert stats.total_rows == 2
    assert stats.translated_rows == 2
    assert stats.errors == 1
    assert "Use %s to start." in html
    assert "使用 %s 开始。" in html
    assert "placeholder_mismatch" in html


def test_write_review_report_respects_issues_only(tmp_path):
    csv_path = tmp_path / "translated.csv"
    output_path = tmp_path / "review.html"
    create_review_csv(csv_path)

    stats = write_review_report(csv_path=csv_path, output_path=output_path, issues_only=True)
    html = output_path.read_text(encoding="utf-8")

    assert stats.displayed_rows == 1
    assert "Use $(item)Quartz$(0)." in html
    assert "Use %s to start." not in html


def test_run_review_writes_default_report(tmp_path):
    csv_path = tmp_path / "translated.csv"
    create_review_csv(csv_path)

    code = run_review(csv_path, output=None, limit=10, issues_only=False)

    assert code == 0
    assert (tmp_path / "translation_review.html").exists()
