from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from hashlib import sha256

from mc_han.models import ExtractedText


class TranslationSource(str, Enum):
    OFFICIAL_ZH_CN = "official_zh_cn"
    MODPACK_AUTHOR = "modpack_author"
    TRUSTED_RESOURCE_PACK = "trusted_resource_pack"
    PROJECT_HISTORY = "project_history"
    LOCAL_MEMORY = "local_memory"
    AI = "ai"
    MANUAL = "manual"


SOURCE_PRIORITY = {
    TranslationSource.AI: 10,
    TranslationSource.LOCAL_MEMORY: 20,
    TranslationSource.PROJECT_HISTORY: 30,
    TranslationSource.TRUSTED_RESOURCE_PACK: 40,
    TranslationSource.OFFICIAL_ZH_CN: 50,
    TranslationSource.MODPACK_AUTHOR: 60,
    TranslationSource.MANUAL: 70,
}


@dataclass(frozen=True)
class ExistingTranslationCandidate:
    record_id: str
    source: TranslationSource
    translation: str
    mod_id: str
    key_path: str
    original_hash: str
    artifact_version: str
    source_location: str

    def __post_init__(self) -> None:
        if not self.record_id:
            raise ValueError("record_id must not be empty")
        if not isinstance(self.source, TranslationSource):
            raise TypeError("source must be TranslationSource")
        for field_name in (
            "translation",
            "mod_id",
            "key_path",
            "original_hash",
            "artifact_version",
            "source_location",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{field_name} must not be empty")

    def matches(
        self,
        record: ExtractedText,
        *,
        mod_id: str,
        artifact_version: str,
    ) -> bool:
        return (
            self.record_id == record.id
            and self.mod_id == mod_id
            and self.key_path == record.key_path
            and self.original_hash == original_text_hash(record.original)
            and self.artifact_version == artifact_version
        )


@dataclass(frozen=True)
class TranslationProvenance:
    record_id: str
    initial_source: TranslationSource
    current_source: TranslationSource
    provider: str
    model: str
    rule_version: str
    first_generated_at: str
    last_modified_at: str
    manual_confirmed_at: str
    original_hash: str
    artifact_version: str
    mod_id: str
    changed_after_update: bool
    translation_hash: str

    def __post_init__(self) -> None:
        if not self.record_id:
            raise ValueError("record_id must not be empty")
        if not isinstance(self.initial_source, TranslationSource):
            raise TypeError("initial_source must be TranslationSource")
        if not isinstance(self.current_source, TranslationSource):
            raise TypeError("current_source must be TranslationSource")
        if type(self.changed_after_update) is not bool:
            raise TypeError("changed_after_update must be bool")
        for field_name in (
            "first_generated_at",
            "last_modified_at",
            "original_hash",
            "artifact_version",
            "mod_id",
            "translation_hash",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{field_name} must not be empty")
        for timestamp in (
            self.first_generated_at,
            self.last_modified_at,
            self.manual_confirmed_at,
        ):
            if timestamp:
                datetime.fromisoformat(timestamp)


def original_text_hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def translated_text_hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def record_mod_id(record: ExtractedText) -> str:
    parts = tuple(part for part in record.file_path.replace("\\", "/").split("/") if part)
    try:
        assets_index = tuple(part.casefold() for part in parts).index("assets")
    except ValueError:
        return "modpack"
    if assets_index + 1 >= len(parts):
        return "modpack"
    return parts[assets_index + 1].casefold()


def record_artifact_version(record: ExtractedText) -> str:
    container = record.container.replace("\\", "/")
    if container.casefold().startswith("mods/"):
        return container.rsplit("/", 1)[-1]
    if container == "modpack":
        path = record.file_path.replace("\\", "/")
        if "/assets/" in f"/{path}":
            prefix = path.split("/assets/", 1)[0]
            return prefix or "modpack"
    return container or "modpack"
