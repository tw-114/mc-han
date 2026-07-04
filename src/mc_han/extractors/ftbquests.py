from __future__ import annotations

import json
import re
from pathlib import Path

from mc_han.extractors.common import (
    decode_escaped_string,
    is_translatable_text,
    should_skip_lang_key,
    strip_wrapping_quotes,
)
from mc_han.extractors.lang_json import extract_lang_json
from mc_han.models import ExtractedText, make_record
from mc_han.utils.encoding import decode_text
from mc_han.utils.paths import relative_posix

KEY_VALUE_RE = re.compile(r"^\s*(?P<key>[^#:=\s][^:=]*?)\s*[:=]\s*(?P<value>.*?)\s*$")
SNBT_STRING_PAIR_RE = re.compile(
    r'(?P<key>"(?:\\.|[^"])+?"|[A-Za-z0-9_.-]+)\s*:\s*"(?P<value>(?:\\.|[^"])*)"',
)
FTBQUEST_TEXT_KEYS = {"title", "subtitle", "description"}
FTBQUEST_DIRECTIVE_RE = re.compile(
    r"^\{(?:@[A-Za-z0-9_.-]+|image:|item:|entity:|recipe:|quest:|chapter:).*}$"
)
SNBT_DESCRIPTION_START_RE = re.compile(r"description\s*:\s*\[")
SNBT_STRING_RE = re.compile(r'"(?P<value>(?:\\.|[^"])*)"')


def scan_ftbquests(modpack_dir: Path) -> list[ExtractedText]:
    records: list[ExtractedText] = []
    records.extend(scan_ftbquests_lang(modpack_dir))
    records.extend(scan_ftbquests_snbt(modpack_dir))
    return records


def scan_ftbquests_lang(modpack_dir: Path) -> list[ExtractedText]:
    lang_root = modpack_dir / "config" / "ftbquests" / "quests" / "lang"
    if not lang_root.exists():
        return []

    paths = discover_en_us_lang_files(lang_root)
    records: list[ExtractedText] = []
    for path in paths:
        rel_path = relative_posix(path, modpack_dir)
        content = decode_text(path.read_bytes())
        records.extend(extract_ftbquests_lang_file(content, file_path=rel_path))
    return records


def scan_ftbquests_snbt(modpack_dir: Path) -> list[ExtractedText]:
    records: list[ExtractedText] = []
    for path in discover_ftbquest_snbt_files(modpack_dir):
        rel_path = relative_posix(path, modpack_dir)
        content = decode_text(path.read_bytes())
        records.extend(extract_ftbquests_snbt(content, file_path=rel_path))
    return records


def discover_ftbquest_snbt_files(modpack_dir: Path) -> list[Path]:
    quests_root = modpack_dir / "config" / "ftbquests" / "quests"
    if not quests_root.exists():
        return []
    lang_root = quests_root / "lang"
    paths: list[Path] = []
    for path in quests_root.rglob("*.snbt"):
        if lang_root in path.parents:
            continue
        paths.append(path)
    return sorted(paths)


def discover_en_us_lang_files(lang_root: Path) -> list[Path]:
    candidates: list[Path] = []
    direct = lang_root / "en_us"
    if direct.is_file():
        candidates.append(direct)
    elif direct.is_dir():
        candidates.extend(path for path in direct.rglob("*") if path.is_file())

    for suffix in ("json", "snbt", "lang", "properties", "txt"):
        path = lang_root / f"en_us.{suffix}"
        if path.is_file():
            candidates.append(path)

    candidates.extend(path for path in lang_root.glob("en_us*") if path.is_file())
    return sorted(set(candidates))


def extract_ftbquests_lang_file(content: str, *, file_path: str) -> list[ExtractedText]:
    stripped = content.lstrip()
    if file_path.endswith(".json") or stripped.startswith("{"):
        try:
            return extract_lang_json(
                content,
                source_type="ftbquests_lang",
                container="modpack",
                file_path=file_path,
            )
        except json.JSONDecodeError:
            pass

    if file_path.endswith(".snbt"):
        snbt_records = extract_snbt_pairs(content, file_path=file_path)
        if snbt_records:
            return snbt_records

    return extract_key_value_lines(content, file_path=file_path)


def extract_ftbquests_snbt(content: str, *, file_path: str) -> list[ExtractedText]:
    records: list[ExtractedText] = []
    consumed_ranges: list[tuple[int, int]] = []

    for array_start, array_end, body_start, body in iter_description_arrays(content):
        consumed_ranges.append((array_start, array_end))
        for index, string_match in enumerate(SNBT_STRING_RE.finditer(body)):
            value = decode_escaped_string(string_match.group("value"))
            if not should_include_ftbquest_text(value):
                continue
            absolute_start = body_start + string_match.start()
            line_number = content.count("\n", 0, absolute_start) + 1
            records.append(
                make_record(
                    source_type="ftbquests_snbt",
                    container="modpack",
                    file_path=file_path,
                    key_path=f"description[{index}]@line:{line_number}",
                    original=value,
                    note=f"snbt line {line_number}",
                )
            )

    for match in SNBT_STRING_PAIR_RE.finditer(content):
        if any(start <= match.start() < end for start, end in consumed_ranges):
            continue
        key = strip_wrapping_quotes(match.group("key"))
        if key not in FTBQUEST_TEXT_KEYS:
            continue
        value = decode_escaped_string(match.group("value"))
        if not should_include_ftbquest_text(value):
            continue
        line_number = content.count("\n", 0, match.start()) + 1
        records.append(
            make_record(
                source_type="ftbquests_snbt",
                container="modpack",
                file_path=file_path,
                key_path=f"{key}@line:{line_number}",
                original=value,
                note=f"snbt line {line_number}",
            )
        )

    return records


def iter_description_arrays(content: str) -> list[tuple[int, int, int, str]]:
    arrays: list[tuple[int, int, int, str]] = []
    for match in SNBT_DESCRIPTION_START_RE.finditer(content):
        open_bracket_index = match.end() - 1
        close_bracket_index = find_matching_snbt_bracket(content, open_bracket_index)
        if close_bracket_index is None:
            continue
        body_start = open_bracket_index + 1
        arrays.append(
            (
                match.start(),
                close_bracket_index + 1,
                body_start,
                content[body_start:close_bracket_index],
            )
        )
    return arrays


def find_matching_snbt_bracket(content: str, open_bracket_index: int) -> int | None:
    depth = 0
    in_string = False
    escaped = False
    for index in range(open_bracket_index, len(content)):
        char = content[index]
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
            continue
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return index
    return None


def should_include_ftbquest_text(value: str) -> bool:
    text = value.strip()
    if FTBQUEST_DIRECTIVE_RE.fullmatch(text):
        return False
    return is_translatable_text(value)


def extract_key_value_lines(content: str, *, file_path: str) -> list[ExtractedText]:
    records: list[ExtractedText] = []
    for line_number, line in enumerate(content.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "//")):
            continue
        match = KEY_VALUE_RE.match(line)
        if not match:
            continue
        key = strip_wrapping_quotes(match.group("key").strip())
        value = strip_wrapping_quotes(match.group("value").rstrip(",").strip())
        if should_skip_lang_key(key):
            continue
        if not is_translatable_text(value):
            continue
        records.append(
            make_record(
                source_type="ftbquests_lang",
                container="modpack",
                file_path=file_path,
                key_path=key,
                original=value,
                note=f"line {line_number}",
            )
        )
    return records


def extract_snbt_pairs(content: str, *, file_path: str) -> list[ExtractedText]:
    records: list[ExtractedText] = []
    for match in SNBT_STRING_PAIR_RE.finditer(content):
        key = strip_wrapping_quotes(match.group("key"))
        value = decode_escaped_string(match.group("value"))
        if should_skip_lang_key(key):
            continue
        if not is_translatable_text(value):
            continue
        line_number = content.count("\n", 0, match.start()) + 1
        records.append(
            make_record(
                source_type="ftbquests_lang",
                container="modpack",
                file_path=file_path,
                key_path=key,
                original=value,
                note=f"snbt line {line_number}",
            )
        )
    return records
