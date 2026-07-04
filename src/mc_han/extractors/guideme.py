from __future__ import annotations

from mc_han.extractors.common import is_translatable_text
from mc_han.models import ExtractedText, make_record


FENCE_PREFIXES = ("```", "~~~")


def extract_markdown(
    content: str,
    *,
    source_type: str,
    container: str,
    file_path: str,
) -> list[ExtractedText]:
    records: list[ExtractedText] = []
    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    in_fence = False
    buffer: list[str] = []
    start_line = 1

    def flush(end_line: int) -> None:
        nonlocal buffer, start_line
        if not buffer:
            return
        paragraph = "\n".join(buffer).strip()
        if is_translatable_text(paragraph):
            records.append(
                make_record(
                    source_type=source_type,
                    container=container,
                    file_path=file_path,
                    key_path=f"lines:{start_line}-{end_line}",
                    original=paragraph,
                    note="markdown paragraph",
                )
            )
        buffer = []

    for index, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith(FENCE_PREFIXES):
            if not in_fence:
                flush(index - 1)
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not stripped:
            flush(index - 1)
            continue
        if not buffer:
            start_line = index
        buffer.append(line)

    flush(len(lines))
    return records
