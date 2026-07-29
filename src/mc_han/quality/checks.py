from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from mc_han.csv_store import read_extracted_csv
from mc_han.extractors.common import CJK_RE, RESOURCE_ID_IN_TEXT_RE, strip_protected_syntax_for_language_check
from mc_han.models import ExtractedText
from mc_han.translator.names import is_name_source, name_translation_keeps_english

from .garbled import has_garbled_text
from .markdown import fenced_code_blocks_closed
from .placeholders import PLACEHOLDER_RE, placeholders_match


@dataclass(frozen=True)
class CheckIssue:
    severity: str
    code: str
    location: str
    message: str


def check_csv(path: Path) -> list[CheckIssue]:
    issues: list[CheckIssue] = []
    records = read_extracted_csv(path)
    for record in records:
        if record.skip_status.strip():
            continue
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
        issues.extend(_local_style_issues(record, location))
    issues.extend(_inconsistent_translation_issues(records))
    issues.extend(_name_body_consistency_issues(records))
    issues.extend(_task_tone_issues(records))
    return _deduplicate_issues(issues)


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
            if (
                file_path.suffix.lower() == ".snbt"
                and not snbt_structure_valid(text)
            ):
                issues.append(
                    CheckIssue(
                        "error",
                        "invalid_snbt",
                        relative,
                        "SNBT braces, brackets, or quoted strings are not balanced.",
                    )
                )
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


def snbt_structure_valid(text: str) -> bool:
    stack: list[str] = []
    pairs = {"}": "{", "]": "["}
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append(char)
        elif char in "}]":
            if not stack or stack.pop() != pairs[char]:
                return False
    return not in_string and not stack


def _local_style_issues(
    record: ExtractedText,
    location: str,
) -> list[CheckIssue]:
    if is_name_source(record.source_type):
        return []
    visible_original = strip_protected_syntax_for_language_check(
        record.original
    ).strip()
    visible_translation = strip_protected_syntax_for_language_check(
        record.translation
    ).strip()
    style_translation = RESOURCE_ID_IN_TEXT_RE.sub(
        "X",
        PLACEHOLDER_RE.sub("X", record.translation),
    )
    issues: list[CheckIssue] = []
    if (
        re.search(r"[\u3400-\u9fff]\s+[\u3400-\u9fff]", style_translation)
        or re.search(r"\s+[，。！？；：]", style_translation)
        or "  " in style_translation
    ):
        issues.append(
            CheckIssue(
                "warning",
                "chinese_spacing",
                location,
                "中文译文中可能存在多余空格。",
            )
        )
    if re.search(r"[\u3400-\u9fff][,!?;:](?:\s|$)", style_translation):
        issues.append(
            CheckIssue(
                "warning",
                "chinese_punctuation",
                location,
                "中文句子可能使用了不一致的半角标点。",
            )
        )
    original_length = len(visible_original)
    translated_length = len(visible_translation)
    if original_length >= 20 and translated_length:
        ratio = translated_length / original_length
        if ratio < 0.18 or ratio > 2.8:
            issues.append(
                CheckIssue(
                    "warning",
                    "abnormal_translation_length",
                    location,
                    "译文长度与原文差异较大，建议人工确认。",
                )
            )
    return issues


def _inconsistent_translation_issues(
    records: list[ExtractedText],
) -> list[CheckIssue]:
    grouped: dict[str, list[ExtractedText]] = defaultdict(list)
    for record in records:
        if (
            record.translation.strip()
            and not record.skip_status.strip()
            and len(record.original.strip()) <= 120
        ):
            grouped[record.original.strip().casefold()].append(record)
    issues: list[CheckIssue] = []
    for grouped_records in grouped.values():
        translations = {
            item.translation.strip() for item in grouped_records
        }
        if len(translations) <= 1:
            continue
        code = (
            "term_translation_inconsistent"
            if len(grouped_records[0].original.split()) <= 4
            else "same_source_multiple_translations"
        )
        for record in grouped_records:
            issues.append(
                CheckIssue(
                    "warning",
                    code,
                    f"{record.file_path} :: {record.key_path}",
                    "相同原文在不同位置使用了不同译法。",
                )
            )
    return issues


def _name_body_consistency_issues(
    records: list[ExtractedText],
) -> list[CheckIssue]:
    names: list[tuple[str, str]] = []
    for record in records:
        if (
            not is_name_source(record.source_type)
            or not record.translation.strip()
            or len(record.original.strip()) < 4
        ):
            continue
        chinese_name = record.translation.split(" (", 1)[0].strip()
        if len(chinese_name) >= 2:
            names.append((record.original.strip(), chinese_name))
    issues: list[CheckIssue] = []
    for record in records:
        if (
            is_name_source(record.source_type)
            or not record.translation.strip()
            or record.skip_status.strip()
        ):
            continue
        for english_name, chinese_name in names:
            if (
                english_name in record.original
                and chinese_name not in record.translation
            ):
                issues.append(
                    CheckIssue(
                        "warning",
                        "name_body_inconsistent",
                        f"{record.file_path} :: {record.key_path}",
                        "正文中的名称译法与显示名称可能不一致。",
                    )
                )
                break
    return issues


def _task_tone_issues(
    records: list[ExtractedText],
) -> list[CheckIssue]:
    by_file: dict[tuple[str, str], list[ExtractedText]] = defaultdict(list)
    for record in records:
        if (
            record.source_type in {"ftbquests_lang", "ftbquests_snbt"}
            and record.translation.strip()
            and not record.skip_status.strip()
        ):
            by_file[(record.container, record.file_path)].append(record)
    issues: list[CheckIssue] = []
    for grouped_records in by_file.values():
        uses_familiar = any("你" in item.translation for item in grouped_records)
        uses_formal = any("您" in item.translation for item in grouped_records)
        if not (uses_familiar and uses_formal):
            continue
        for record in grouped_records:
            if "你" in record.translation or "您" in record.translation:
                issues.append(
                    CheckIssue(
                        "warning",
                        "task_tone_inconsistent",
                        f"{record.file_path} :: {record.key_path}",
                        "同一任务文件中同时使用了“你”和“您”。",
                    )
                )
    return issues


def _deduplicate_issues(issues: list[CheckIssue]) -> list[CheckIssue]:
    unique = {
        (issue.severity, issue.code, issue.location, issue.message): issue
        for issue in issues
    }
    severity_order = {"error": 0, "warning": 1, "info": 2}
    return sorted(
        unique.values(),
        key=lambda issue: (
            severity_order.get(issue.severity, 3),
            issue.code,
            issue.location.casefold(),
            issue.location,
            issue.message,
        ),
    )
