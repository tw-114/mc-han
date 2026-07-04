from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from mc_han.config import NAME_LANG_KEY_PREFIXES, SKIPPED_LANG_KEY_PREFIXES

ENGLISH_WORD_RE = re.compile(r"[A-Za-z]{3,}")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
MIN_MIXED_TEXT_ENGLISH_CHARS = 24
MINECRAFT_COLOR_CODE_RE = re.compile(r"(?:§|&)[0-9A-FK-ORa-fk-or]")
PRINTF_PLACEHOLDER_RE = re.compile(r"%(?:\d+\$)?[sdif]")
BRACE_PLACEHOLDER_RE = re.compile(r"\{\d+\}")
TEMPLATE_PLACEHOLDER_RE = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_.-]*\}")
PATCHOULI_TAG_RE = re.compile(r"\$\([^)]+\)")
XMLISH_TAG_RE = re.compile(r"</?[A-Za-z][A-Za-z0-9]*(?:\s+[^<>]*?)?>")
RESOURCE_ID_RE = re.compile(r"^#?[a-z0-9_.-]+:[a-z0-9_/.-]+$")
RESOURCE_ID_IN_TEXT_RE = re.compile(r"#?[a-z0-9_.-]+:[a-z0-9_/.-]+")
FILE_PATH_RE = re.compile(r"^(?:[a-z0-9_.-]+/)+[a-z0-9_.-]+(?:\.[a-z0-9]+)?$", re.IGNORECASE)
PLACEHOLDER_ONLY_RE = re.compile(r"^[\s%{}$()#&§:._/\-0-9A-Za-z]+$")

SKIPPED_JSON_VALUE_KEYS = {
    "advancement",
    "anchor",
    "block",
    "category",
    "entity",
    "entity_id",
    "flag",
    "icon",
    "id",
    "input",
    "item",
    "link",
    "loot_table",
    "modifier",
    "output",
    "parent",
    "recipe",
    "template",
    "trigger",
    "type",
}


def should_skip_lang_key(key: str) -> bool:
    return key.startswith(SKIPPED_LANG_KEY_PREFIXES)


def is_name_lang_key(key: str) -> bool:
    return key.startswith(NAME_LANG_KEY_PREFIXES)


def should_skip_lang_key_for_mode(key: str, *, translate_names: bool) -> bool:
    if translate_names and is_name_lang_key(key):
        return False
    return should_skip_lang_key(key)


def is_translatable_text(value: str) -> bool:
    text = value.strip()
    if not text:
        return False
    visible_text = strip_protected_syntax_for_language_check(text)
    if not ENGLISH_WORD_RE.search(visible_text):
        return False
    if CJK_RE.search(visible_text):
        english_chars = sum(len(word) for word in ENGLISH_WORD_RE.findall(visible_text))
        if english_chars < MIN_MIXED_TEXT_ENGLISH_CHARS:
            return False
    if RESOURCE_ID_RE.fullmatch(text):
        return False
    if FILE_PATH_RE.fullmatch(text):
        return False
    if PLACEHOLDER_ONLY_RE.fullmatch(text) and not any(ch.isspace() for ch in text):
        return False
    return True


def is_translatable_name_text(value: str) -> bool:
    text = value.strip()
    if not text:
        return False
    if CJK_RE.search(text):
        return False
    if RESOURCE_ID_RE.fullmatch(text):
        return False
    if FILE_PATH_RE.fullmatch(text):
        return False
    visible_text = strip_protected_syntax_for_language_check(text)
    return bool(ENGLISH_WORD_RE.search(visible_text))


def strip_protected_syntax_for_language_check(value: str) -> str:
    text = MINECRAFT_COLOR_CODE_RE.sub("", value)
    text = PRINTF_PLACEHOLDER_RE.sub("", text)
    text = BRACE_PLACEHOLDER_RE.sub("", text)
    text = TEMPLATE_PLACEHOLDER_RE.sub("", text)
    text = PATCHOULI_TAG_RE.sub("", text)
    text = XMLISH_TAG_RE.sub("", text)
    text = RESOURCE_ID_IN_TEXT_RE.sub("", text)
    return text


def should_skip_json_value(key_path: str, value: str) -> bool:
    leaf = key_path.rsplit(".", 1)[-1]
    leaf = leaf.rsplit("[", 1)[0]
    if leaf in SKIPPED_JSON_VALUE_KEYS:
        return True
    if RESOURCE_ID_RE.fullmatch(value.strip()):
        return True
    return False


def iter_json_strings(data: Any, key_path: str = "") -> Iterator[tuple[str, str]]:
    if isinstance(data, dict):
        for key, value in data.items():
            child_key = str(key)
            child_path = f"{key_path}.{child_key}" if key_path else child_key
            yield from iter_json_strings(value, child_path)
    elif isinstance(data, list):
        for index, value in enumerate(data):
            child_path = f"{key_path}[{index}]" if key_path else f"[{index}]"
            yield from iter_json_strings(value, child_path)
    elif isinstance(data, str):
        yield key_path, data


def strip_wrapping_quotes(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    return text


def decode_escaped_string(value: str) -> str:
    if "\\" not in value:
        return value

    def replace_match(match: re.Match[str]) -> str:
        escape = match.group(1)
        if escape == "n":
            return "\n"
        if escape == "r":
            return "\r"
        if escape == "t":
            return "\t"
        if escape == '"':
            return '"'
        if escape == "\\":
            return "\\"
        if escape.startswith("u") and len(escape) == 5:
            try:
                return chr(int(escape[1:], 16))
            except ValueError:
                return "\\" + escape
        return "\\" + escape

    return re.sub(r"\\(u[0-9A-Fa-f]{4}|.)", replace_match, value)
