from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from mc_han.quality.checks import check_output_dir


@dataclass(frozen=True)
class InstallPlanItem:
    source: Path
    target: Path
    relative_target: str
    category: str
    will_overwrite: bool


@dataclass(frozen=True)
class InstallPlan:
    modpack_dir: Path
    build_dir: Path
    items: list[InstallPlanItem]

    @property
    def total_files(self) -> int:
        return len(self.items)

    @property
    def overwrite_files(self) -> int:
        return sum(1 for item in self.items if item.will_overwrite)

    @property
    def new_files(self) -> int:
        return self.total_files - self.overwrite_files


@dataclass(frozen=True)
class InstallResult:
    installed_files: int
    backed_up_files: int
    backup_dir: Path
    manifest_path: Path | None = None


@dataclass(frozen=True)
class RollbackResult:
    restored_files: int
    removed_files: int
    backup_dir: Path
    manifest_path: Path


def plan_install_outputs(*, modpack_dir: Path, build_dir: Path) -> InstallPlan:
    modpack_dir = Path(modpack_dir)
    build_dir = Path(build_dir)
    items_by_target: dict[Path, InstallPlanItem] = {}

    add_tree_to_plan(
        items_by_target,
        source_root=build_dir / "resourcepacks" / "mc-han-cn",
        target_root=modpack_dir / "resourcepacks" / "mc-han-cn",
        category="client_resourcepack",
    )
    add_tree_to_plan(
        items_by_target,
        source_root=build_dir / "mc-han-client-resourcepack",
        target_root=modpack_dir / "resourcepacks" / "mc-han-cn",
        category="client_resourcepack",
    )
    add_tree_to_plan(
        items_by_target,
        source_root=build_dir / "mc-han-complete-install" / "resourcepacks" / "mc-han-cn",
        target_root=modpack_dir / "resourcepacks" / "mc-han-cn",
        category="client_resourcepack",
    )

    add_tree_to_plan(
        items_by_target,
        source_root=build_dir / "config",
        target_root=modpack_dir / "config",
        category="server_config",
    )
    add_tree_to_plan(
        items_by_target,
        source_root=build_dir / "mc-han-server-pack" / "config",
        target_root=modpack_dir / "config",
        category="server_config",
    )
    add_tree_to_plan(
        items_by_target,
        source_root=build_dir / "mc-han-complete-install" / "config",
        target_root=modpack_dir / "config",
        category="server_config",
    )

    return InstallPlan(
        modpack_dir=modpack_dir,
        build_dir=build_dir,
        items=sorted(items_by_target.values(), key=lambda item: item.relative_target),
    )


def write_install_plan_report(plan: InstallPlan, report_path: Path) -> None:
    lines = [
        "mc-han install dry-run report",
        f"modpack_dir: {plan.modpack_dir}",
        f"build_dir: {plan.build_dir}",
        f"total_files: {plan.total_files}",
        f"new_files: {plan.new_files}",
        f"overwrite_files: {plan.overwrite_files}",
        "",
        "targets",
    ]
    if not plan.items:
        lines.append("  none")
    for item in plan.items:
        action = "overwrite" if item.will_overwrite else "new"
        lines.append(f"  [{action}] [{item.category}] {item.relative_target}")
    lines.extend(
        [
            "",
            "Multiplayer note:",
            "  Install the server pack on the server when config files are present.",
            "  Each player should enable the client resource pack.",
            "  PCL2 LAN hosting with FRP tunneling still means the hosting player is the server.",
            "  For Windows/PCL2 servers, consider JVM arg: -Dfile.encoding=UTF-8",
        ]
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def install_outputs(*, modpack_dir: Path, build_dir: Path) -> InstallResult:
    modpack_dir = Path(modpack_dir)
    build_dir = Path(build_dir)
    issues = check_output_dir(build_dir)
    errors = [issue for issue in issues if issue.severity == "error"]
    if errors:
        raise RuntimeError("Build output has quality errors; install refused.")

    plan = plan_install_outputs(modpack_dir=modpack_dir, build_dir=build_dir)
    if not plan.items:
        raise RuntimeError("No installable client resource pack or server config files were found.")

    backup_dir = modpack_dir / ".mc-han" / "backups" / datetime.now().strftime("%Y%m%d-%H%M%S")
    installed_files = 0
    backed_up_files = 0
    manifest_items: list[dict[str, object]] = []

    for item in plan.items:
        had_backup = item.target.exists()
        backup_relative = item.relative_target if had_backup else ""
        if had_backup:
            backed_up_files += backup_existing(item.target, modpack_dir, backup_dir)
        item.target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item.source, item.target)
        installed_files += 1
        manifest_items.append(
            {
                "source": str(item.source),
                "target": str(item.target),
                "relative_target": item.relative_target,
                "category": item.category,
                "had_backup": had_backup,
                "backup_relative": backup_relative,
            }
        )

    manifest_path = write_install_manifest(
        backup_dir=backup_dir,
        modpack_dir=modpack_dir,
        build_dir=build_dir,
        items=manifest_items,
    )

    write_install_report(
        build_dir=build_dir,
        installed_files=installed_files,
        backed_up_files=backed_up_files,
        backup_dir=backup_dir,
        manifest_path=manifest_path,
    )
    write_install_plan_report(plan, build_dir / "install_plan.txt")
    return InstallResult(
        installed_files=installed_files,
        backed_up_files=backed_up_files,
        backup_dir=backup_dir,
        manifest_path=manifest_path,
    )


def rollback_install(*, modpack_dir: Path, backup_dir: Path | None = None) -> RollbackResult:
    modpack_dir = Path(modpack_dir)
    resolved_backup_dir = Path(backup_dir) if backup_dir else latest_backup_dir(modpack_dir)
    manifest_path = resolved_backup_dir / "install_manifest.json"
    if not manifest_path.exists():
        raise RuntimeError(f"Install manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    restored_files = 0
    removed_files = 0

    for item in reversed(manifest.get("items", [])):
        relative_target = item.get("relative_target", "")
        if not isinstance(relative_target, str) or not relative_target:
            continue
        target = modpack_dir / Path(*relative_target.split("/"))
        had_backup = bool(item.get("had_backup"))
        if had_backup:
            backup_source = resolved_backup_dir / Path(*relative_target.split("/"))
            if backup_source.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup_source, target)
                restored_files += 1
        elif target.exists():
            target.unlink()
            removed_files += 1

    report_path = resolved_backup_dir / "rollback_report.txt"
    report_path.write_text(
        "\n".join(
            [
                "mc-han rollback report",
                f"backup_dir: {resolved_backup_dir}",
                f"restored_files: {restored_files}",
                f"removed_files: {removed_files}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return RollbackResult(
        restored_files=restored_files,
        removed_files=removed_files,
        backup_dir=resolved_backup_dir,
        manifest_path=manifest_path,
    )


def backup_existing(path: Path, modpack_dir: Path, backup_dir: Path) -> int:
    if not path.exists():
        return 0
    relative = path.relative_to(modpack_dir)
    backup_path = backup_dir / relative
    if path.is_dir():
        if backup_path.exists():
            shutil.rmtree(backup_path)
        shutil.copytree(path, backup_path)
        return sum(1 for child in backup_path.rglob("*") if child.is_file())
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup_path)
    return 1


def add_tree_to_plan(
    items_by_target: dict[Path, InstallPlanItem],
    *,
    source_root: Path,
    target_root: Path,
    category: str,
) -> None:
    if not source_root.exists():
        return
    for source_path in sorted(path for path in source_root.rglob("*") if path.is_file()):
        relative = source_path.relative_to(source_root)
        target_path = target_root / relative
        resolved_target = target_path.resolve()
        if resolved_target in items_by_target:
            continue
        items_by_target[resolved_target] = InstallPlanItem(
            source=source_path,
            target=target_path,
            relative_target=target_path.relative_to(target_root.parents[1]).as_posix()
            if category == "client_resourcepack"
            else target_path.relative_to(target_root.parent).as_posix(),
            category=category,
            will_overwrite=target_path.exists(),
        )


def copy_tree(src: Path, dst: Path) -> int:
    count = 0
    for src_path in sorted(path for path in src.rglob("*") if path.is_file()):
        relative = src_path.relative_to(src)
        dst_path = dst / relative
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_path, dst_path)
        count += 1
    return count


def write_install_manifest(
    *,
    backup_dir: Path,
    modpack_dir: Path,
    build_dir: Path,
    items: list[dict[str, object]],
) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = backup_dir / "install_manifest.json"
    manifest = {
        "version": 1,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "modpack_dir": str(modpack_dir),
        "build_dir": str(build_dir),
        "items": items,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def latest_backup_dir(modpack_dir: Path) -> Path:
    backups_dir = Path(modpack_dir) / ".mc-han" / "backups"
    candidates = sorted(path for path in backups_dir.glob("*") if (path / "install_manifest.json").exists())
    if not candidates:
        raise RuntimeError(f"No mc-han install backups found in {backups_dir}")
    return candidates[-1]


def write_install_report(
    *,
    build_dir: Path,
    installed_files: int,
    backed_up_files: int,
    backup_dir: Path,
    manifest_path: Path | None = None,
) -> None:
    lines = [
        "mc-han install report",
        f"installed_files: {installed_files}",
        f"backed_up_files: {backed_up_files}",
        f"backup_dir: {backup_dir}",
        f"manifest_path: {manifest_path or ''}",
        "",
        "Multiplayer note:",
        "  Clients need the generated resource pack.",
        "  Servers need the generated config overlays when FTB Quests/config files are included.",
        "  PCL2 LAN hosting with FRP tunneling still means the hosting player is the server.",
        "  For Windows/PCL2 servers, consider JVM arg: -Dfile.encoding=UTF-8",
    ]
    (Path(build_dir) / "install_report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
