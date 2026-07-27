from __future__ import annotations

import re

from .version import get_version


_ALPHA_VERSION = re.compile(r"^(?P<base>\d+\.\d+\.\d+)a(?P<number>\d+)$")


def release_tag(version: str | None = None) -> str:
    package_version = version or get_version()
    match = _ALPHA_VERSION.fullmatch(package_version)
    if match is not None:
        package_version = f"{match.group('base')}-alpha.{match.group('number')}"
    return f"v{package_version}"


def windows_archive_name(version: str | None = None) -> str:
    return f"mc-han-windows-x64-{release_tag(version)}.zip"


def windows_checksum_name(version: str | None = None) -> str:
    return f"{windows_archive_name(version)}.sha256"


def about_text(version: str | None = None) -> str:
    return (
        f"mc-han {version or get_version()}\n\n"
        "Minecraft 整合包自动汉化工具\n"
        "当前 Windows 预览版仅接入整合包检测界面。\n\n"
        "源代码：https://github.com/tw-114/mc-han"
    )
