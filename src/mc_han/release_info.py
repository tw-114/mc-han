from __future__ import annotations

import re

from .version import get_version


_PRERELEASE_VERSION = re.compile(
    r"^(?P<base>\d+\.\d+\.\d+)(?P<label>a|b)(?P<number>\d+)$"
)
_PRERELEASE_LABELS = {"a": "alpha", "b": "beta"}


def release_tag(version: str | None = None) -> str:
    package_version = version or get_version()
    match = _PRERELEASE_VERSION.fullmatch(package_version)
    if match is not None:
        label = _PRERELEASE_LABELS[match.group("label")]
        package_version = f"{match.group('base')}-{label}.{match.group('number')}"
    return f"v{package_version}"


def windows_archive_name(version: str | None = None) -> str:
    return f"mc-han-windows-x64-{release_tag(version)}.zip"


def windows_checksum_name(version: str | None = None) -> str:
    return f"mc-han-windows-x64-{release_tag(version)}.sha256"


def about_text(version: str | None = None) -> str:
    return (
        f"mc-han {version or get_version()}\n\n"
        "Minecraft 整合包自动汉化工具\n"
        "Windows MVP 预览版已接入检测、翻译、检查、构建与安装流程。\n\n"
        "源代码：https://github.com/tw-114/mc-han"
    )
