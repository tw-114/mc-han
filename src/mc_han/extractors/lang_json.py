from __future__ import annotations

import json

from mc_han.extractors.common import (
    is_name_lang_key,
    is_translatable_name_text,
    is_translatable_text,
    should_skip_lang_key_for_mode,
)
from mc_han.models import ExtractedText, make_record


def extract_lang_json(
    content: str,
    *,
    source_type: str,
    container: str,
    file_path: str,
    translate_names: bool = False,
    allow_name_keys: bool = False,
) -> list[ExtractedText]:
    data = json.loads(content)
    if not isinstance(data, dict):
        return []

    records: list[ExtractedText] = []
    for key in sorted(data):
        value = data[key]
        if not isinstance(value, str):
            continue
        is_name_key = is_name_lang_key(key)
        if should_skip_lang_key_for_mode(key, translate_names=translate_names and allow_name_keys):
            continue
        if is_name_key:
            if not translate_names or not allow_name_keys:
                continue
            if not is_translatable_name_text(value):
                continue
            records.append(
                make_record(
                    source_type="lang_name",
                    container=container,
                    file_path=file_path,
                    key_path=key,
                    original=value,
                    note="lang display name; keep English original",
                )
            )
            continue
        if not is_translatable_text(value):
            continue
        records.append(
            make_record(
                source_type=source_type,
                container=container,
                file_path=file_path,
                key_path=key,
                original=value,
                note="lang json value",
            )
        )
    return records


def extract_flat_lang_json(
    content: str,
    *,
    source_type: str,
    container: str,
    file_path: str,
    translate_names: bool = False,
    allow_name_keys: bool = False,
) -> list[ExtractedText]:
    return extract_lang_json(
        content,
        source_type=source_type,
        container=container,
        file_path=file_path,
        translate_names=translate_names,
        allow_name_keys=allow_name_keys,
    )
