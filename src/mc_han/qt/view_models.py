from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath, PureWindowsPath

from mc_han.workflow.models import (
    CAPABILITY_ORDER,
    ChineseResourceStatus,
    ContentCapability,
    InspectionMessage,
    InspectionValidity,
    ModpackInspection,
)


class WorkflowStage(str, Enum):
    WELCOME = "welcome"
    INSPECTING = "inspecting"
    INSPECTION_RESULT = "inspection_result"
    SCANNING = "scanning"
    SCAN_RESULT = "scan_result"
    TRANSLATION_CONFIG = "translation_config"
    TRIAL_TRANSLATION = "trial_translation"
    FULL_TRANSLATION_PLACEHOLDER = "full_translation_placeholder"


class StatusTone(str, Enum):
    NEUTRAL = "neutral"
    PRIMARY = "primary"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class CapabilityCardViewModel:
    key: str
    title: str
    status_text: str
    detail_text: str
    tone: StatusTone
    detected: bool


@dataclass(frozen=True)
class MessageViewModel:
    severity: str
    title: str
    message: str
    location: str
    tone: StatusTone


@dataclass(frozen=True)
class ChineseResourceViewModel:
    title: str
    detail_text: str
    tone: StatusTone


@dataclass(frozen=True)
class InspectionPageViewModel:
    status_title: str
    status_description: str
    status_tone: StatusTone
    can_continue: bool
    display_name: str
    minecraft_version: str
    loader: str
    mod_count: str
    duration: str
    capabilities: tuple[CapabilityCardViewModel, ...]
    existing_chinese: ChineseResourceViewModel
    messages: tuple[MessageViewModel, ...]

    @classmethod
    def from_inspection(
        cls,
        inspection: ModpackInspection,
    ) -> "InspectionPageViewModel":
        status_title, status_description, status_tone = _inspection_status(
            inspection.validity
        )
        capability_by_key = {
            capability.key: capability
            for capability in inspection.capabilities
        }
        capabilities = tuple(
            _capability_view_model(capability_by_key[key])
            for key in CAPABILITY_ORDER
            if key in capability_by_key
        )
        loader = _display_value(inspection.loader_name)
        if inspection.loader_version != "unknown":
            loader = f"{loader} {inspection.loader_version}"
        return cls(
            status_title=status_title,
            status_description=status_description,
            status_tone=status_tone,
            can_continue=inspection.can_continue,
            display_name=_display_value(inspection.display_name),
            minecraft_version=_display_value(inspection.minecraft_version),
            loader=loader,
            mod_count=str(inspection.mod_count),
            duration=f"{inspection.inspection_duration:.2f} 秒",
            capabilities=capabilities,
            existing_chinese=_chinese_resource_view_model(
                inspection.existing_chinese.status,
                inspection.existing_chinese.item_count,
                inspection.existing_chinese.source_count,
            ),
            messages=tuple(_message_view_model(message) for message in inspection.messages),
        )


def _inspection_status(
    validity: InspectionValidity,
) -> tuple[str, str, StatusTone]:
    if validity is InspectionValidity.VALID:
        return "已识别整合包", "目录结构和实例元数据可用于后续处理。", StatusTone.SUCCESS
    if validity is InspectionValidity.PROBABLE:
        return (
            "疑似 Minecraft 整合包",
            "检测到实例迹象，但部分版本或加载器信息尚不完整。",
            StatusTone.WARNING,
        )
    return (
        "无法识别为整合包",
        "请选择包含 mods、config 或实例元数据的整合包根目录。",
        StatusTone.ERROR,
    )


def _capability_view_model(
    capability: ContentCapability,
) -> CapabilityCardViewModel:
    if not capability.detected:
        return CapabilityCardViewModel(
            key=capability.key,
            title=capability.label,
            status_text="未检测到",
            detail_text="当前整合包中暂未发现此类内容",
            tone=StatusTone.NEUTRAL,
            detected=False,
        )
    if capability.item_count == 0:
        return CapabilityCardViewModel(
            key=capability.key,
            title=capability.label,
            status_text="已检测到",
            detail_text="已检测到目录，但暂未发现具体文件",
            tone=StatusTone.PRIMARY,
            detected=True,
        )
    return CapabilityCardViewModel(
        key=capability.key,
        title=capability.label,
        status_text="已检测到",
        detail_text=(
            f"{capability.item_count} 个相关文件 · "
            f"来自 {capability.source_count} 个来源"
        ),
        tone=StatusTone.SUCCESS,
        detected=True,
    )


def _chinese_resource_view_model(
    status: ChineseResourceStatus,
    item_count: int,
    source_count: int,
) -> ChineseResourceViewModel:
    if status is ChineseResourceStatus.PARTIAL:
        return ChineseResourceViewModel(
            title="检测到部分中文资源",
            detail_text=f"{item_count} 个相关文件 · 来自 {source_count} 个来源",
            tone=StatusTone.PRIMARY,
        )
    if status is ChineseResourceStatus.UNKNOWN:
        return ChineseResourceViewModel(
            title="无法完整判断",
            detail_text="部分 JAR 无法安全读取，请查看下方警告",
            tone=StatusTone.WARNING,
        )
    return ChineseResourceViewModel(
        title="未发现中文资源",
        detail_text="可在后续扫描中创建新的汉化内容",
        tone=StatusTone.NEUTRAL,
    )


def _message_view_model(message: InspectionMessage) -> MessageViewModel:
    title_by_severity = {
        "error": "需要处理",
        "warning": "请注意",
        "info": "提示",
    }
    tone_by_severity = {
        "error": StatusTone.ERROR,
        "warning": StatusTone.WARNING,
        "info": StatusTone.PRIMARY,
    }
    return MessageViewModel(
        severity=message.severity,
        title=title_by_severity.get(message.severity, "提示"),
        message=message.message,
        location=_safe_relative_location(message.location),
        tone=tone_by_severity.get(message.severity, StatusTone.NEUTRAL),
    )


def _display_value(value: str) -> str:
    return "未识别" if not value or value == "unknown" else value


def _safe_relative_location(value: str) -> str:
    if not value or value == ".":
        return ""
    windows_path = PureWindowsPath(value)
    posix_path = PurePosixPath(value)
    if (
        windows_path.drive
        or windows_path.root
        or windows_path.is_absolute()
        or posix_path.is_absolute()
        or "\x00" in value
        or any(not character.isprintable() for character in value)
    ):
        return ""
    return value
