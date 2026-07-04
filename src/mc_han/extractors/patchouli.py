from __future__ import annotations

import json

from mc_han.extractors.common import (
    is_translatable_text,
    iter_json_strings,
    should_skip_json_value,
)
from mc_han.models import ExtractedText, make_record


def extract_patchouli_json(
    content: str,
    *,
    source_type: str,
    container: str,
    file_path: str,
) -> list[ExtractedText]:
    data = json.loads(content)
    records: list[ExtractedText] = []
    for key_path, value in iter_json_strings(data):
        if should_skip_json_value(key_path, value):
            continue
        if not is_translatable_text(value):
            continue
        records.append(
            make_record(
                source_type=source_type,
                container=container,
                file_path=file_path,
                key_path=key_path,
                original=value,
                note="patchouli json value",
            )
        )
    return records
