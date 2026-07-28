from __future__ import annotations

from dataclasses import dataclass

from mc_han.builder.installer import InstallResult, RollbackResult
from mc_han.services.build_install import (
    BuildWorkflowResult,
    ExportWorkflowResult,
)


@dataclass(frozen=True)
class BuildInstallPageViewModel:
    output_name: str
    entry_text: str
    resource_text: str
    config_text: str
    install_text: str
    pack_format_text: str
    output_directory: str
    warnings: tuple[str, ...]
    errors: tuple[str, ...]
    can_export: bool
    can_install: bool

    @classmethod
    def ready(
        cls,
        *,
        output_directory: str,
    ) -> "BuildInstallPageViewModel":
        return cls(
            output_name="生成后显示",
            entry_text="-",
            resource_text="-",
            config_text="-",
            install_text="-",
            pack_format_text="-",
            output_directory=output_directory,
            warnings=(),
            errors=(),
            can_export=False,
            can_install=False,
        )

    @classmethod
    def from_result(
        cls,
        result: BuildWorkflowResult,
    ) -> "BuildInstallPageViewModel":
        usable = not result.errors and result.installable_files > 0
        return cls(
            output_name=result.output_file_name,
            entry_text=f"{result.installable_files:,}",
            resource_text=f"{result.resource_files:,}",
            config_text=f"{result.config_files:,}",
            install_text=(
                f"{result.new_files:,} 个新文件 · "
                f"{result.overwrite_files:,} 个覆盖"
            ),
            pack_format_text=str(result.pack_format),
            output_directory=str(result.output_dir),
            warnings=result.warnings,
            errors=result.errors,
            can_export=usable,
            can_install=usable,
        )


@dataclass(frozen=True)
class CompletionPageViewModel:
    title: str
    detail: str
    location: str
    can_rollback: bool

    @classmethod
    def exported(
        cls,
        result: ExportWorkflowResult,
    ) -> "CompletionPageViewModel":
        return cls(
            title="ZIP 已导出",
            detail=(
                f"文件大小：{_format_size(result.archive_size)}。"
                "可以发送给其他玩家或手动解压安装。"
            ),
            location=str(result.archive_path),
            can_rollback=False,
        )

    @classmethod
    def installed(
        cls,
        result: InstallResult,
    ) -> "CompletionPageViewModel":
        return cls(
            title="汉化包已安装",
            detail=(
                f"已安装 {result.installed_files:,} 个文件，"
                f"备份 {result.backed_up_files:,} 个原有文件。"
            ),
            location=(
                str(result.manifest_path)
                if result.manifest_path is not None
                else str(result.backup_dir)
            ),
            can_rollback=result.manifest_path is not None,
        )

    @classmethod
    def rolled_back(
        cls,
        result: RollbackResult,
    ) -> "CompletionPageViewModel":
        return cls(
            title="本次安装已撤销",
            detail=(
                f"恢复 {result.restored_files:,} 个文件，"
                f"移除 {result.removed_files:,} 个新文件。"
            ),
            location=str(result.manifest_path),
            can_rollback=False,
        )


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"
