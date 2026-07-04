from __future__ import annotations

SKIPPED_LANG_KEY_PREFIXES: tuple[str, ...] = (
    "item.",
    "block.",
    "entity.",
    "fluid.",
    "biome.",
    "effect.",
    "enchantment.",
    "sound_event.",
    "death.attack.",
)

NAME_LANG_KEY_PREFIXES: tuple[str, ...] = (
    "item.",
    "block.",
    "entity.",
    "fluid.",
    "biome.",
    "effect.",
    "enchantment.",
)

DEFAULT_OUTPUT_CSV = "extracted_texts.csv"
DEFAULT_NAME_TRANSLATION_FORMAT = "{zh} ({en})"
