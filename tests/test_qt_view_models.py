from __future__ import annotations

from pathlib import Path

import pytest

from mc_han.qt.view_models import InspectionPageViewModel, StatusTone
from mc_han.workflow.models import (
    CAPABILITY_ORDER,
    ChineseResourceStatus,
    ContentCapability,
    ExistingChineseResources,
    InspectionMessage,
    InspectionValidity,
    LoaderInfo,
    ModpackInspection,
)


def make_inspection(
    *,
    validity: InspectionValidity = InspectionValidity.VALID,
    minecraft_version: str = "1.20.1",
    loader: LoaderInfo = LoaderInfo("NeoForge", "47.1.0"),
    capabilities: tuple[ContentCapability, ...] | None = None,
    chinese: ExistingChineseResources | None = None,
    messages: tuple[InspectionMessage, ...] = (),
) -> ModpackInspection:
    capability_values = capabilities or tuple(
        ContentCapability(
            key=key,
            label={
                "mod_language": "模组语言文件",
                "ftb_quests": "FTB Quests",
                "patchouli": "Patchouli",
                "modonomicon": "Modonomicon",
                "guideme": "GuideME / AE2 指南",
                "config_text": "配置与脚本语言文本",
            }[key],
            detected=False,
        )
        for key in CAPABILITY_ORDER
    )
    return ModpackInspection(
        input_directory=Path("pack"),
        validity=validity,
        display_name="Test Pack",
        minecraft_version=minecraft_version,
        loader=loader,
        mod_count=42,
        capabilities=capability_values,
        existing_chinese=chinese
        or ExistingChineseResources(ChineseResourceStatus.NONE),
        messages=messages,
        evidence=(),
        inspection_duration=0.45,
    )


@pytest.mark.parametrize(
    ("validity", "tone", "can_continue", "title"),
    [
        (
            InspectionValidity.VALID,
            StatusTone.SUCCESS,
            True,
            "已识别整合包",
        ),
        (
            InspectionValidity.PROBABLE,
            StatusTone.WARNING,
            True,
            "疑似 Minecraft 整合包",
        ),
        (
            InspectionValidity.INVALID,
            StatusTone.ERROR,
            False,
            "无法识别为整合包",
        ),
    ],
)
def test_inspection_status_mapping(
    validity: InspectionValidity,
    tone: StatusTone,
    can_continue: bool,
    title: str,
):
    view_model = InspectionPageViewModel.from_inspection(
        make_inspection(validity=validity)
    )

    assert view_model.status_tone is tone
    assert view_model.can_continue is can_continue
    assert view_model.status_title == title


def test_unknown_fields_have_user_friendly_fallback():
    view_model = InspectionPageViewModel.from_inspection(
        make_inspection(
            minecraft_version="unknown",
            loader=LoaderInfo(),
        )
    )

    assert view_model.minecraft_version == "未识别"
    assert view_model.loader == "未识别"
    assert "None" not in vars(view_model).values()


def test_capability_order_and_count_copy():
    capabilities = tuple(
        ContentCapability(
            key=key,
            label=key,
            detected=True,
            item_count=2,
            source_count=1,
            sources=(f"mods/{key}.jar",),
        )
        for key in reversed(CAPABILITY_ORDER)
    )

    view_model = InspectionPageViewModel.from_inspection(
        make_inspection(capabilities=capabilities)
    )

    assert tuple(item.key for item in view_model.capabilities) == CAPABILITY_ORDER
    assert all(
        item.detail_text == "2 个相关文件 · 来自 1 个来源"
        for item in view_model.capabilities
    )


def test_detected_empty_capability_has_directory_copy():
    capabilities = (
        ContentCapability(
            key="ftb_quests",
            label="FTB Quests",
            detected=True,
        ),
    )

    view_model = InspectionPageViewModel.from_inspection(
        make_inspection(capabilities=capabilities)
    )

    assert view_model.capabilities[0].detail_text == "已检测到目录，但暂未发现具体文件"


@pytest.mark.parametrize(
    ("resource", "title", "tone"),
    [
        (
            ExistingChineseResources(ChineseResourceStatus.NONE),
            "未发现中文资源",
            StatusTone.NEUTRAL,
        ),
        (
            ExistingChineseResources(
                ChineseResourceStatus.PARTIAL,
                item_count=2,
                source_count=1,
                sources=("mods/demo.jar",),
            ),
            "检测到部分中文资源",
            StatusTone.PRIMARY,
        ),
        (
            ExistingChineseResources(ChineseResourceStatus.UNKNOWN),
            "无法完整判断",
            StatusTone.WARNING,
        ),
    ],
)
def test_existing_chinese_status_mapping(
    resource: ExistingChineseResources,
    title: str,
    tone: StatusTone,
):
    view_model = InspectionPageViewModel.from_inspection(
        make_inspection(chinese=resource)
    )

    assert view_model.existing_chinese.title == title
    assert view_model.existing_chinese.tone is tone


def test_message_absolute_location_is_not_displayed():
    messages = (
        InspectionMessage(
            severity="warning",
            code="test",
            message="安全提示",
            location=r"C:\Users\Private\pack",
        ),
        InspectionMessage(
            severity="info",
            code="relative",
            message="相对位置",
            location="mods/demo.jar",
        ),
    )

    view_model = InspectionPageViewModel.from_inspection(
        make_inspection(messages=messages)
    )
    locations = {
        message.message: message.location
        for message in view_model.messages
    }

    assert locations["安全提示"] == ""
    assert locations["相对位置"] == "mods/demo.jar"
    assert "Private" not in repr(view_model.messages)
