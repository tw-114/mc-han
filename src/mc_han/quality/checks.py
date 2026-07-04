from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from mc_han.csv_store import read_extracted_csv
from mc_han.extractors.common import CJK_RE, RESOURCE_ID_IN_TEXT_RE, strip_protected_syntax_for_language_check
from mc_han.translator.names import is_name_source, name_translation_keeps_english

from .garbled import has_garbled_text
from .markdown import fenced_code_blocks_closed
from .placeholders import placeholders_match


@dataclass(frozen=True)
class CheckIssue:
    severity: str
    code: str
    location: str
    message: str


def check_csv(path: Path) -> list[CheckIssue]:
    issues: list[CheckIssue] = []
    for record in read_extracted_csv(path):
        location = f"{record.file_path} :: {record.key_path}"
        if not record.translation.strip():
            issues.append(CheckIssue("warning", "missing_translation", location, "Translation is empty."))
            continue
        if has_garbled_text(record.translation):
            issues.append(CheckIssue("error", "garbled_text", location, "Translation contains likely mojibake."))
        if not placeholders_match(record.original, record.translation):
            issues.append(
                CheckIssue("error", "placeholder_mismatch", location, "Placeholder/tag/color-code sets differ.")
            )
        if extract_resource_ids(record.original) != extract_resource_ids(record.translation):
            issues.append(CheckIssue("error", "resource_id_changed", location, "Resource IDs differ after translation."))
        if is_name_source(record.source_type) and not name_translation_keeps_english(record.original, record.translation):
            issues.append(
                CheckIssue(
                    "error",
                    "name_english_original_missing",
                    location,
                    "Display-name translation must keep the English original in parentheses.",
                )
            )
        if looks_like_untranslated_english(record.translation):
            issues.append(
                CheckIssue("warning", "english_residue", location, "Translation still appears to contain English prose.")
            )
    return issues


def check_output_dir(path: Path) -> list[CheckIssue]:
    issues: list[CheckIssue] = []
    root = Path(path)
    for file_path in sorted(root.rglob("*")):
        if not file_path.is_file():
            continue
        relative = file_path.relative_to(root).as_posix()
        if file_path.suffix.lower() == ".json":
            try:
                json.loads(file_path.read_text(encoding="utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                issues.append(CheckIssue("error", "invalid_json", relative, str(error)))
        elif file_path.suffix.lower() == ".md":
            try:
                text = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError as error:
                issues.append(CheckIssue("error", "invalid_utf8", relative, str(error)))
                continue
            if not fenced_code_blocks_closed(text):
                issues.append(CheckIssue("error", "markdown_fence_unclosed", relative, "Code fence is not closed."))
        else:
            try:
                text = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
        if file_path.suffix.lower() in {".json", ".md", ".snbt", ".txt", ".lang"}:
            text = file_path.read_text(encoding="utf-8", errors="replace")
            if has_garbled_text(text):
                issues.append(CheckIssue("error", "garbled_text", relative, "File contains likely mojibake."))
    return issues


def write_quality_report(issues: list[CheckIssue], report_path: Path) -> None:
    report_path = Path(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    errors = sum(1 for issue in issues if issue.severity == "error")
    warnings = sum(1 for issue in issues if issue.severity == "warning")
    lines = [
        "mc-han quality report",
        f"errors: {errors}",
        f"warnings: {warnings}",
        "",
    ]
    if not issues:
        lines.append("No issues found.")
    else:
        for issue in issues:
            lines.append(f"[{issue.severity}] {issue.code} :: {issue.location}")
            lines.append(f"  {issue.message}")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def extract_resource_ids(text: str) -> set[str]:
    return set(RESOURCE_ID_IN_TEXT_RE.findall(text))


def looks_like_untranslated_english(text: str) -> bool:
    visible = strip_protected_syntax_for_language_check(text)
    if CJK_RE.search(visible):
        return False
    words = [word for word in visible.replace("_", " ").split() if any(ch.isalpha() for ch in word)]
    return len(words) >= 4
