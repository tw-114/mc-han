from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class TranslationSegment:
    id: str
    text: str
    source_type: str = ""
    file_path: str = ""
    key_path: str = ""
    instructions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        instructions = tuple(self.instructions)
        if not all(isinstance(item, str) and item.strip() for item in instructions):
            raise ValueError("instructions must contain non-empty strings")
        object.__setattr__(self, "instructions", instructions)


class Translator(Protocol):
    provider_name: str
    model: str

    def translate_batch(self, segments: list[TranslationSegment]) -> list[str]:
        """Translate a batch of segments and return translations in the same order."""
