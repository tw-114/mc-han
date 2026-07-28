from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from mc_han.builder.installer import (
    InstallResult,
    RollbackResult,
    install_outputs,
    plan_install_outputs,
    rollback_install,
)
from mc_han.builder.resourcepack import (
    build_complete_install_package,
    build_outputs,
    resource_pack_format_for_version,
)
from mc_han.quality.checks import check_output_dir


EXPORT_ARCHIVE_NAME = "mc-han-cn.zip"


@dataclass(frozen=True)
class BuildWorkflowResult:
    output_dir: Path
    output_file_name: str
    resource_files: int
    config_files: int
    translated_rows: int
    installable_files: int
    new_files: int
    overwrite_files: int
    pack_format: int
    warnings: tuple[str, ...]
    errors: tuple[str, ...]


@dataclass(frozen=True)
class ExportWorkflowResult:
    archive_path: Path
    archive_size: int


def build_localization_package(
    *,
    modpack_dir: Path,
    csv_path: Path,
    output_dir: Path,
    minecraft_version: str,
) -> BuildWorkflowResult:
    stats = build_outputs(
        modpack_dir=modpack_dir,
        csv_path=csv_path,
        output_dir=output_dir,
        minecraft_version=minecraft_version,
    )
    build_complete_install_package(
        output_dir=output_dir,
        translate_names=bool(stats["name_rows"]),
    )
    plan = plan_install_outputs(
        modpack_dir=modpack_dir,
        build_dir=output_dir,
    )
    issues = check_output_dir(output_dir)
    pack_format, known_format = resource_pack_format_for_version(
        minecraft_version
    )
    warnings = [
        f"{issue.code}: {issue.location}"
        for issue in issues
        if issue.severity == "warning"
    ]
    if not known_format:
        warnings.append(
            "无法确定当前 Minecraft 版本的资源包格式，"
            f"已使用兼容默认值 {pack_format}。"
        )
    errors = tuple(
        f"{issue.code}: {issue.location}"
        for issue in issues
        if issue.severity == "error"
    )
    return BuildWorkflowResult(
        output_dir=Path(output_dir),
        output_file_name=EXPORT_ARCHIVE_NAME,
        resource_files=stats["resource_files"],
        config_files=stats["config_files"],
        translated_rows=stats["translated_rows"],
        installable_files=plan.total_files,
        new_files=plan.new_files,
        overwrite_files=plan.overwrite_files,
        pack_format=pack_format,
        warnings=tuple(warnings),
        errors=errors,
    )


def export_localization_zip(
    *,
    output_dir: Path,
) -> ExportWorkflowResult:
    output_dir = Path(output_dir)
    package_root = output_dir / "mc-han-complete-install"
    if not package_root.is_dir():
        package_root = build_complete_install_package(
            output_dir=output_dir,
        )
    if not package_root.is_dir():
        raise RuntimeError("没有可导出的完整安装包。")
    final_path = output_dir / EXPORT_ARCHIVE_NAME
    temporary_base = output_dir / f".mc-han-export-{uuid4().hex}"
    temporary_zip = temporary_base.with_suffix(".zip")
    try:
        archive_name = shutil.make_archive(
            str(temporary_base),
            "zip",
            root_dir=package_root,
        )
        temporary_zip = Path(archive_name)
        os.replace(temporary_zip, final_path)
    finally:
        try:
            temporary_zip.unlink(missing_ok=True)
        except OSError:
            pass
    return ExportWorkflowResult(
        archive_path=final_path,
        archive_size=final_path.stat().st_size,
    )


def install_localization_package(
    *,
    modpack_dir: Path,
    output_dir: Path,
) -> InstallResult:
    return install_outputs(
        modpack_dir=modpack_dir,
        build_dir=output_dir,
    )


def rollback_localization_install(
    *,
    modpack_dir: Path,
    backup_dir: Path,
) -> RollbackResult:
    return rollback_install(
        modpack_dir=modpack_dir,
        backup_dir=backup_dir,
    )
