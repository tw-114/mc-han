from __future__ import annotations


def fenced_code_blocks_closed(text: str) -> bool:
    backtick_count = 0
    tilde_count = 0
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            backtick_count += 1
        elif stripped.startswith("~~~"):
            tilde_count += 1
    return backtick_count % 2 == 0 and tilde_count % 2 == 0
