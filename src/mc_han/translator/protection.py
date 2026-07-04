from __future__ import annotations

import re
from dataclasses import dataclass

from mc_han.extractors.common import RESOURCE_ID_IN_TEXT_RE
from mc_han.quality.placeholders import PLACEHOLDER_RE

PROTECTED_RE = re.compile(
    PLACEHOLDER_RE.pattern + r"|" + RESOURCE_ID_IN_TEXT_RE.pattern,
)


@dataclass(frozen=True)
class ProtectedText:
    text: str
    replacements: tuple[tuple[str, str], ...]

    def restore(self, translated: str) -> str:
        restored = translated
        for token, original in self.replacements:
            restored = restored.replace(token, original)
        return restored


def protect_text(text: str) -> ProtectedText:
    replacements: list[tuple[str, str]] = []

    def replace(match: re.Match[str]) -> str:
        token = f"__MC_HAN_PROTECTED_{len(replacements)}__"
        replacements.append((token, match.group(0)))
        return token

    return ProtectedText(text=PROTECTED_RE.sub(replace, text), replacements=tuple(replacements))
