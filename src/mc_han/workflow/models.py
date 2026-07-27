from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any


CAPABILITY_ORDER = (
    "mod_language",
    "ftb_quests",
    "patchouli",
    "modonomicon",
    "guideme",
    "config_text",
)


class InspectionValidity(str, Enum):
    VALID = "valid"
    PROBABLE = "probable"
    INVALID = "invalid"


class ChineseResourceStatus(str, Enum):
    NONE = "none"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class LoaderInfo:
    name: str = "unknown"
    version: str = "unknown"

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not isinstance(self.version, str):
            raise TypeError("loader name and version must be strings")

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "version": self.version,
        }


@dataclass(frozen=True)
class ContentCapability:
    key: str
    label: str
    detected: bool
    item_count: int = 0
    source_count: int = 0
    sources: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or not isinstance(self.label, str):
            raise TypeError("capability key and label must be strings")
        if type(self.detected) is not bool:
            raise TypeError("detected must be a bool")
        if type(self.item_count) is not int or type(self.source_count) is not int:
            raise TypeError("capability counts must be integers")
        sources = _normalize_sources(self.sources)
        if self.item_count < 0 or self.source_count < 0:
            raise ValueError("item_count and source_count must not be negative")
        if self.source_count != len(sources):
            raise ValueError("source_count must match the number of unique sources")
        if not self.detected and (self.item_count or self.source_count or sources):
            raise ValueError(
                "detected=false requires item_count=0, source_count=0, and empty sources"
            )
        if self.item_count > 0 and self.source_count == 0:
            raise ValueError("item_count>0 requires source_count>0")
        if self.source_count > self.item_count:
            raise ValueError("source_count must not exceed item_count")
        object.__setattr__(self, "sources", sources)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "detected": self.detected,
            "item_count": self.item_count,
            "source_count": self.source_count,
            "sources": list(self.sources),
        }


@dataclass(frozen=True)
class ExistingChineseResources:
    status: ChineseResourceStatus
    item_count: int = 0
    source_count: int = 0
    sources: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.status, ChineseResourceStatus):
            raise TypeError("status must be ChineseResourceStatus")
        if type(self.item_count) is not int or type(self.source_count) is not int:
            raise TypeError("Chinese resource counts must be integers")
        sources = _normalize_sources(self.sources)
        if self.item_count < 0 or self.source_count < 0:
            raise ValueError("item_count and source_count must not be negative")
        if self.source_count != len(sources):
            raise ValueError("source_count must match the number of unique sources")
        if self.source_count > self.item_count:
            raise ValueError("source_count must not exceed item_count")
        if self.status is ChineseResourceStatus.PARTIAL:
            if self.item_count == 0 or self.source_count == 0 or not sources:
                raise ValueError(
                    "status=partial requires positive item_count, source_count, and sources"
                )
        elif self.item_count or self.source_count or sources:
            raise ValueError(
                "status=none or status=unknown requires zero counts and empty sources"
            )
        object.__setattr__(self, "sources", sources)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "item_count": self.item_count,
            "source_count": self.source_count,
            "sources": list(self.sources),
        }


@dataclass(frozen=True)
class InspectionMessage:
    severity: str
    code: str
    message: str
    location: str = ""

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, str)
            for value in (self.severity, self.code, self.message, self.location)
        ):
            raise TypeError("inspection message fields must be strings")

    def to_dict(self) -> dict[str, str]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "location": self.location,
        }


@dataclass(frozen=True)
class ModpackInspection:
    input_directory: Path
    validity: InspectionValidity
    display_name: str
    minecraft_version: str
    loader: LoaderInfo
    mod_count: int
    capabilities: tuple[ContentCapability, ...]
    existing_chinese: ExistingChineseResources
    messages: tuple[InspectionMessage, ...]
    evidence: tuple[str, ...]
    inspection_duration: float

    def __post_init__(self) -> None:
        if not isinstance(self.validity, InspectionValidity):
            raise TypeError("validity must be InspectionValidity")
        if not isinstance(self.display_name, str) or not isinstance(
            self.minecraft_version,
            str,
        ):
            raise TypeError("display_name and minecraft_version must be strings")
        if type(self.mod_count) is not int or self.mod_count < 0:
            raise ValueError("mod_count must be a non-negative integer")
        if isinstance(self.inspection_duration, bool) or not isinstance(
            self.inspection_duration,
            (int, float),
        ):
            raise TypeError("inspection_duration must be numeric")
        if not isinstance(self.loader, LoaderInfo):
            raise TypeError("loader must be LoaderInfo")
        if not isinstance(self.existing_chinese, ExistingChineseResources):
            raise TypeError("existing_chinese must be ExistingChineseResources")

        capabilities = tuple(self.capabilities)
        if not all(isinstance(item, ContentCapability) for item in capabilities):
            raise TypeError("capabilities must contain only ContentCapability values")
        order = {key: index for index, key in enumerate(CAPABILITY_ORDER)}
        capabilities = tuple(
            sorted(
                capabilities,
                key=lambda item: (order.get(item.key, len(order)), item.key),
            )
        )

        messages = tuple(self.messages)
        if not all(isinstance(item, InspectionMessage) for item in messages):
            raise TypeError("messages must contain only InspectionMessage values")
        severity_order = {"error": 0, "warning": 1, "info": 2}
        messages = tuple(
            sorted(
                messages,
                key=lambda item: (
                    severity_order.get(item.severity, 3),
                    item.code,
                    _stable_text_key(item.location),
                    item.message,
                ),
            )
        )
        evidence_values = tuple(self.evidence)
        if not all(isinstance(item, str) for item in evidence_values):
            raise TypeError("evidence must contain only strings")
        evidence = tuple(sorted(set(evidence_values), key=_stable_text_key))

        object.__setattr__(self, "input_directory", Path(self.input_directory))
        object.__setattr__(self, "capabilities", capabilities)
        object.__setattr__(self, "messages", messages)
        object.__setattr__(self, "evidence", evidence)

    @property
    def loader_name(self) -> str:
        return self.loader.name

    @property
    def loader_version(self) -> str:
        return self.loader.version

    @property
    def can_continue(self) -> bool:
        return self.validity is not InspectionValidity.INVALID

    def to_dict(self) -> dict[str, Any]:
        return {
            # The runtime model retains the resolved path for a future GUI, while
            # persisted/JSON output uses only its final component.
            "input_directory": self.input_directory.name or ".",
            "validity": self.validity.value,
            "display_name": self.display_name,
            "minecraft_version": self.minecraft_version,
            "loader_name": self.loader_name,
            "loader_version": self.loader_version,
            "loader": self.loader.to_dict(),
            "mod_count": self.mod_count,
            "capabilities": [capability.to_dict() for capability in self.capabilities],
            "existing_chinese": self.existing_chinese.to_dict(),
            "messages": [message.to_dict() for message in self.messages],
            "evidence": list(self.evidence),
            "inspection_duration": round(self.inspection_duration, 6),
            "can_continue": self.can_continue,
        }


def _stable_text_key(value: str) -> tuple[str, str]:
    return value.casefold(), value


def _normalize_sources(values: tuple[str, ...]) -> tuple[str, ...]:
    sources = tuple(values)
    if not all(isinstance(item, str) for item in sources):
        raise TypeError("sources must contain only strings")
    if any(not item for item in sources):
        raise ValueError("sources must not contain empty strings")
    if len(set(sources)) != len(sources):
        raise ValueError("sources must be unique")
    return tuple(sorted(sources, key=_stable_text_key))
