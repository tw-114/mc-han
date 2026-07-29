from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from mc_han.config import DEFAULT_NAME_TRANSLATION_FORMAT
from mc_han.utils.atomic_json import write_json_atomic


@dataclass(frozen=True)
class UserSettings:
    provider: str | None = None
    model: str | None = None
    api_key: str | None = None
    api_key_env: str | None = None
    base_url: str | None = None
    limit: int | None = None
    speed_mode: str | None = None
    worker_count: int | None = None
    max_batch_items: int | None = None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    timeout_seconds: int | None = None
    translate_names: bool | None = None
    name_translation_format: str | None = None


def config_path() -> Path:
    override = os.environ.get("MC_HAN_CONFIG")
    if override:
        return Path(override).expanduser()
    config_dir = os.environ.get("MC_HAN_CONFIG_DIR")
    if config_dir:
        return Path(config_dir).expanduser() / "config.json"
    appdata = os.environ.get("APPDATA")
    if os.name == "nt" and appdata:
        return Path(appdata) / "mc-han" / "config.json"
    return Path.home() / ".config" / "mc-han" / "config.json"


def load_settings(path: Path | None = None) -> UserSettings:
    resolved = path or config_path()
    if not resolved.exists():
        return UserSettings()
    try:
        raw = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return UserSettings()
    if not isinstance(raw, dict):
        return UserSettings()
    return UserSettings(
        provider=clean_optional_str(raw.get("provider")),
        model=clean_optional_str(raw.get("model")),
        # Legacy plaintext API keys are deliberately ignored.
        api_key=None,
        api_key_env=clean_optional_str(raw.get("api_key_env")),
        base_url=clean_optional_str(raw.get("base_url")),
        limit=clean_optional_int(raw.get("limit")),
        speed_mode=clean_speed_mode(raw.get("speed_mode")),
        worker_count=clean_bounded_int(raw.get("worker_count"), minimum=1, maximum=3),
        max_batch_items=clean_optional_int(raw.get("max_batch_items")),
        max_input_tokens=clean_optional_int(raw.get("max_input_tokens")),
        max_output_tokens=clean_optional_int(raw.get("max_output_tokens")),
        timeout_seconds=clean_bounded_int(
            raw.get("timeout_seconds"),
            minimum=1,
            maximum=600,
        ),
        translate_names=clean_optional_bool(raw.get("translate_names")),
        name_translation_format=clean_optional_str(raw.get("name_translation_format")),
    )


def save_settings(settings: UserSettings, path: Path | None = None) -> Path:
    resolved = path or config_path()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    data = {
        key: value
        for key, value in asdict(settings).items()
        if key != "api_key" and value not in (None, "")
    }
    write_json_atomic(resolved, data)
    try:
        os.chmod(resolved, 0o600)
    except OSError:
        pass
    return resolved


def clear_settings(path: Path | None = None) -> bool:
    resolved = path or config_path()
    if not resolved.exists():
        return False
    resolved.unlink()
    return True


def merge_settings(base: UserSettings, **updates: Any) -> UserSettings:
    data = asdict(base)
    for key, value in updates.items():
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip()
            if not value:
                continue
        data[key] = value
    return UserSettings(**data)


def masked_api_key(api_key: str | None) -> str:
    if not api_key:
        return ""
    if len(api_key) <= 8:
        return "*" * len(api_key)
    return f"{api_key[:4]}...{api_key[-4:]}"


def clean_optional_str(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def clean_optional_int(value: object) -> int | None:
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
        return parsed if parsed > 0 else None
    return None


def clean_bounded_int(value: object, *, minimum: int, maximum: int) -> int | None:
    parsed = clean_optional_int(value)
    if parsed is None:
        return None
    if parsed < minimum or parsed > maximum:
        return None
    return parsed


def clean_speed_mode(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized if normalized in {"safe", "balanced", "fast"} else None


def clean_optional_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return None


def default_name_translation_format() -> str:
    return DEFAULT_NAME_TRANSLATION_FORMAT
