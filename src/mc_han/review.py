from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from html import escape
from pathlib import Path

from .csv_store import read_extracted_csv
from .models import ExtractedText
from .quality.checks import CheckIssue, check_csv


@dataclass(frozen=True)
class ReviewStats:
    total_rows: int
    translated_rows: int
    missing_rows: int
    displayed_rows: int
    errors: int
    warnings: int


def write_review_report(
    *,
    csv_path: Path,
    output_path: Path,
    limit: int | None = 1000,
    issues_only: bool = False,
) -> ReviewStats:
    html, stats = build_review_html(csv_path=csv_path, limit=limit, issues_only=issues_only)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return stats


def build_review_html(
    *,
    csv_path: Path,
    limit: int | None = 1000,
    issues_only: bool = False,
) -> tuple[str, ReviewStats]:
    records = read_extracted_csv(csv_path)
    issues = check_csv(csv_path)
    issues_by_location = group_issues_by_location(issues)
    source_counts = Counter(record.source_type for record in records)
    translated_rows = sum(1 for record in records if record.translation.strip())
    missing_rows = len(records) - translated_rows
    errors = sum(1 for issue in issues if issue.severity == "error")
    warnings = sum(1 for issue in issues if issue.severity == "warning")
    visible_records = filter_review_records(records, issues_by_location, issues_only=issues_only)
    if limit is not None and limit >= 0:
        visible_records = visible_records[:limit]

    stats = ReviewStats(
        total_rows=len(records),
        translated_rows=translated_rows,
        missing_rows=missing_rows,
        displayed_rows=len(visible_records),
        errors=errors,
        warnings=warnings,
    )
    html = render_review_html(
        csv_path=Path(csv_path),
        records=visible_records,
        source_counts=source_counts,
        issues_by_location=issues_by_location,
        stats=stats,
        limit=limit,
        issues_only=issues_only,
    )
    return html, stats


def filter_review_records(
    records: list[ExtractedText],
    issues_by_location: dict[str, list[CheckIssue]],
    *,
    issues_only: bool,
) -> list[ExtractedText]:
    if not issues_only:
        return records
    return [record for record in records if row_location(record) in issues_by_location]


def group_issues_by_location(issues: list[CheckIssue]) -> dict[str, list[CheckIssue]]:
    grouped: dict[str, list[CheckIssue]] = defaultdict(list)
    for issue in issues:
        grouped[issue.location].append(issue)
    return dict(grouped)


def render_review_html(
    *,
    csv_path: Path,
    records: list[ExtractedText],
    source_counts: Counter[str],
    issues_by_location: dict[str, list[CheckIssue]],
    stats: ReviewStats,
    limit: int | None,
    issues_only: bool,
) -> str:
    source_rows = "\n".join(
        f"<tr><td>{escape(source_type)}</td><td>{count}</td></tr>" for source_type, count in source_counts.most_common()
    )
    rows_html = "\n".join(render_record_row(record, issues_by_location) for record in records)
    limit_text = "all rows" if limit is None or limit < 0 else str(limit)
    mode_text = "issues only" if issues_only else "all rows"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>mc-han translation review</title>
  <style>
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
      color: #1f2933;
      background: #f5f7fa;
    }}
    header {{
      padding: 20px 28px;
      background: #17202a;
      color: #f8fafc;
    }}
    main {{ padding: 20px 28px 36px; }}
    h1 {{ margin: 0 0 8px; font-size: 24px; }}
    h2 {{ margin-top: 24px; font-size: 18px; }}
    .meta {{ color: #d5dde8; overflow-wrap: anywhere; }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 10px;
      margin: 18px 0;
    }}
    .stat {{
      background: white;
      border: 1px solid #d9e2ec;
      border-radius: 8px;
      padding: 12px;
    }}
    .stat strong {{ display: block; font-size: 22px; margin-bottom: 4px; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: white;
      border: 1px solid #d9e2ec;
    }}
    th, td {{
      text-align: left;
      vertical-align: top;
      border-bottom: 1px solid #e6edf5;
      padding: 8px 10px;
    }}
    th {{ background: #edf2f7; }}
    .review-row td {{ font-size: 13px; }}
    .source {{ min-width: 130px; }}
    .path {{ color: #52606d; overflow-wrap: anywhere; }}
    .text {{
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      line-height: 1.45;
    }}
    .missing {{ color: #9a3412; }}
    .issue {{
      display: block;
      margin: 2px 0;
      padding: 4px 6px;
      border-radius: 5px;
      font-size: 12px;
    }}
    .error {{ background: #fee2e2; color: #991b1b; }}
    .warning {{ background: #fef3c7; color: #92400e; }}
    .ok {{ color: #166534; }}
  </style>
</head>
<body>
  <header>
    <h1>mc-han 翻译审阅报告</h1>
    <div class="meta">CSV: {escape(str(csv_path))}</div>
    <div class="meta">Mode: {escape(mode_text)} | Display limit: {escape(limit_text)}</div>
  </header>
  <main>
    <section class="stats">
      {render_stat("总条目", stats.total_rows)}
      {render_stat("已翻译", stats.translated_rows)}
      {render_stat("未翻译", stats.missing_rows)}
      {render_stat("显示条目", stats.displayed_rows)}
      {render_stat("错误", stats.errors)}
      {render_stat("警告", stats.warnings)}
    </section>

    <h2>来源统计</h2>
    <table>
      <thead><tr><th>来源</th><th>数量</th></tr></thead>
      <tbody>{source_rows}</tbody>
    </table>

    <h2>原文 / 译文对照</h2>
    <table>
      <thead>
        <tr>
          <th class="source">来源</th>
          <th>定位</th>
          <th>原文</th>
          <th>译文</th>
          <th>问题</th>
        </tr>
      </thead>
      <tbody>
        {rows_html}
      </tbody>
    </table>
  </main>
</body>
</html>
"""


def render_stat(label: str, value: int) -> str:
    return f'<div class="stat"><strong>{value}</strong>{escape(label)}</div>'


def render_record_row(record: ExtractedText, issues_by_location: dict[str, list[CheckIssue]]) -> str:
    location = row_location(record)
    issues = issues_by_location.get(location, [])
    issue_html = render_issues(issues)
    translation = record.translation if record.translation else "未翻译"
    translation_class = "text" if record.translation else "text missing"
    return f"""<tr class="review-row">
  <td>{escape(record.source_type)}<br>{escape(record.container)}</td>
  <td class="path">{escape(record.file_path)}<br>{escape(record.key_path)}</td>
  <td class="text">{escape(record.original)}</td>
  <td class="{translation_class}">{escape(translation)}</td>
  <td>{issue_html}</td>
</tr>"""


def render_issues(issues: list[CheckIssue]) -> str:
    if not issues:
        return '<span class="ok">OK</span>'
    return "".join(
        f'<span class="issue {escape(issue.severity)}">{escape(issue.code)}: {escape(issue.message)}</span>'
        for issue in issues
    )


def row_location(record: ExtractedText) -> str:
    return f"{record.file_path} :: {record.key_path}"
