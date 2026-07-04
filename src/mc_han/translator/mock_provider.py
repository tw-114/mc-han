from __future__ import annotations

import re

from .base import TranslationSegment
from .names import is_name_source, normalize_name_translation
from .protection import protect_text

MARKDOWN_PREFIX_RE = re.compile(r"^(\s{0,3}(?:#{1,6}\s+|[-*+]\s+|\d+\.\s+|>\s+))")


class MockTranslator:
    provider_name = "mock"
    model = "mock"

    def translate_batch(self, segments: list[TranslationSegment]) -> list[str]:
        return [
            normalize_name_translation(segment.text, mock_translate_name(segment.text))
            if is_name_source(segment.source_type)
            else mock_translate(segment.text)
            for segment in segments
        ]


def mock_translate(text: str) -> str:
    protected = protect_text(text)
    prefix = ""
    body = protected.text
    match = MARKDOWN_PREFIX_RE.match(body)
    if match:
        prefix = match.group(1)
        body = body[len(prefix) :]
    tokens = [token for token, _original in protected.replacements]
    translated = prefix + "模拟译文"
    if tokens:
        translated += " " + " ".join(tokens)
    if "\n" in body:
        translated += "\n模拟译文"
    return protected.restore(translated)


def mock_translate_name(text: str) -> str:
    words = {
        "Engineer": "工程师",
        "Multimeter": "万用表",
        "Mining": "采矿",
        "Drill": "钻头",
        "Crusher": "粉碎机",
        "Quantum": "量子",
        "Computer": "计算机",
        "Spatial": "空间",
        "Pylon": "塔柱",
        "Wrench": "扳手",
    }
    cleaned = re.sub(r"[^A-Za-z0-9]+", " ", text).strip()
    translated = "".join(words.get(part, "") for part in cleaned.split())
    return translated or "模拟名称"
