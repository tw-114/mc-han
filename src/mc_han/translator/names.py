from __future__ import annotations

import re

from mc_han.config import DEFAULT_NAME_TRANSLATION_FORMAT
from mc_han.extractors.common import CJK_RE

NAME_SOURCE_TYPE = "lang_name"


def is_name_source(source_type: str) -> bool:
    return source_type == NAME_SOURCE_TYPE


def normalize_name_translation(
    original: str,
    translation: str,
    *,
    name_translation_format: str = DEFAULT_NAME_TRANSLATION_FORMAT,
) -> str:
    original = original.strip()
    text = translation.strip()
    if not original or not text:
        return translation
    if CJK_RE.search(original):
        return text

    text = collapse_duplicate_english_suffix(original, text)
    if has_english_original_suffix(original, text):
        return text

    zh = remove_inline_original(original, text).strip()
    if not zh:
        zh = text
    return name_translation_format.replace("{zh}", zh).replace("{en}", original)


def has_english_original_suffix(original: str, translation: str) -> bool:
    original = original.strip()
    if not original:
        return True
    pattern = r"\(\s*" + re.escape(original) + r"\s*\)\s*$"
    return re.search(pattern, translation.strip()) is not None


def name_translation_keeps_english(original: str, translation: str) -> bool:
    if not original.strip() or CJK_RE.search(original):
        return True
    return has_english_original_suffix(original, translation)


def collapse_duplicate_english_suffix(original: str, translation: str) -> str:
    text = translation.strip()
    suffix_pattern = r"\s*\(\s*" + re.escape(original.strip()) + r"\s*\)\s*$"
    suffixes = 0
    while re.search(suffix_pattern, text):
        suffixes += 1
        text = re.sub(suffix_pattern, "", text).strip()
    if suffixes == 0:
        return translation.strip()
    return f"{text} ({original.strip()})" if text else f"({original.strip()})"


def remove_inline_original(original: str, translation: str) -> str:
    text = translation.strip()
    original = original.strip()
    if not original:
        return text
    text = text.replace(original, "").strip()
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\(\s*\)", "", text).strip()
    return text
