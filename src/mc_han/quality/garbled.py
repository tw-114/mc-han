from __future__ import annotations

GARBLED_MARKERS = ("锟斤拷", "Ã", "Â", "å", "æ", "ç")


def has_garbled_text(text: str) -> bool:
    return any(marker in text for marker in GARBLED_MARKERS)
