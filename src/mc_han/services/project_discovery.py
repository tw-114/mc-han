from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from mc_han.utils.safe_paths import (
    UnsafePathError,
    resolve_path_for_operation,
)


INSTANCE_METADATA = frozenset(
    {
        "manifest.json",
        "minecraftinstance.json",
        "mmc-pack.json",
        "instance.cfg",
        "modrinth.index.json",
        "profile.json",
        "pack.toml",
    }
)
INSTANCE_DIRECTORIES = frozenset({"mods", "config", "resourcepacks", "kubejs"})


@dataclass(frozen=True)
class DiscoveryLocation:
    launcher: str
    path: Path
    include_root: bool = False


@dataclass(frozen=True)
class DiscoveredProject:
    path: Path
    display_name: str
    launcher: str
    evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", Path(self.path))
        object.__setattr__(self, "evidence", tuple(sorted(set(self.evidence))))


def default_discovery_locations(
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> tuple[DiscoveryLocation, ...]:
    values = dict(os.environ if environ is None else environ)
    user_home = Path(home) if home is not None else Path.home()
    appdata = Path(values.get("APPDATA", user_home / "AppData" / "Roaming"))
    profile = Path(values.get("USERPROFILE", user_home))
    documents = Path(values.get("USERPROFILE", user_home)) / "Documents"
    return (
        DiscoveryLocation(
            "Minecraft / PCL2 / HMCL",
            appdata / ".minecraft",
            include_root=True,
        ),
        DiscoveryLocation(
            "PCL2 / HMCL",
            appdata / ".minecraft" / "versions",
        ),
        DiscoveryLocation(
            "CurseForge",
            profile / "curseforge" / "minecraft" / "Instances",
        ),
        DiscoveryLocation(
            "CurseForge",
            documents / "Curse" / "Minecraft" / "Instances",
        ),
        DiscoveryLocation(
            "CurseForge",
            appdata / "CurseForge" / "minecraft" / "Instances",
        ),
        DiscoveryLocation(
            "Prism Launcher",
            appdata / "PrismLauncher" / "instances",
        ),
        DiscoveryLocation(
            "Prism Launcher",
            appdata / "prismlauncher" / "instances",
        ),
        DiscoveryLocation(
            "MultiMC",
            appdata / "MultiMC" / "instances",
        ),
        DiscoveryLocation(
            "Modrinth",
            appdata / "com.modrinth.theseus" / "profiles",
        ),
    )


def discover_modpacks(
    *,
    locations: Sequence[DiscoveryLocation] | None = None,
    manual_paths: Sequence[Path] = (),
) -> tuple[DiscoveredProject, ...]:
    discovered: dict[str, DiscoveredProject] = {}
    roots = tuple(locations or default_discovery_locations())
    for location in roots:
        _discover_location(location, discovered)
    for manual_path in manual_paths:
        candidate = _inspect_candidate(
            Path(manual_path),
            launcher="手动添加",
            display_name=Path(manual_path).name,
        )
        if candidate is not None:
            discovered.setdefault(_identity(candidate.path), candidate)
    return tuple(
        sorted(
            discovered.values(),
            key=lambda item: (
                item.display_name.casefold(),
                item.launcher.casefold(),
                _identity(item.path),
            ),
        )
    )


def _discover_location(
    location: DiscoveryLocation,
    discovered: dict[str, DiscoveredProject],
) -> None:
    root = Path(location.path)
    if not _safe_directory(root):
        return
    if location.include_root:
        candidate = _inspect_candidate(
            root,
            launcher=location.launcher,
            display_name=root.name or "Minecraft",
        )
        if candidate is not None:
            discovered.setdefault(_identity(candidate.path), candidate)
    try:
        entries = sorted(root.iterdir(), key=lambda item: item.name.casefold())
    except OSError:
        return
    for entry in entries:
        try:
            safe_entry = resolve_path_for_operation(
                root,
                entry.name,
                label="launcher instance",
            )
        except (OSError, RuntimeError, UnsafePathError):
            continue
        if not _safe_directory(safe_entry):
            continue
        game_directory = _nested_game_directory(safe_entry)
        candidate = _inspect_candidate(
            game_directory,
            launcher=location.launcher,
            display_name=safe_entry.name,
        )
        if candidate is not None:
            discovered.setdefault(_identity(candidate.path), candidate)


def _nested_game_directory(instance_directory: Path) -> Path:
    nested = instance_directory / ".minecraft"
    if _safe_directory(nested) and _instance_evidence(nested):
        return nested
    return instance_directory


def _inspect_candidate(
    path: Path,
    *,
    launcher: str,
    display_name: str,
) -> DiscoveredProject | None:
    if not _safe_directory(path):
        return None
    evidence = _instance_evidence(path)
    if not evidence:
        return None
    return DiscoveredProject(
        path=path,
        display_name=display_name or path.name or "Minecraft",
        launcher=launcher,
        evidence=evidence,
    )


def _instance_evidence(path: Path) -> tuple[str, ...]:
    evidence: list[str] = []
    for name in sorted(INSTANCE_METADATA | INSTANCE_DIRECTORIES):
        candidate = path / name
        try:
            metadata = candidate.lstat()
        except OSError:
            continue
        if _is_reparse(metadata):
            continue
        if name in INSTANCE_METADATA and stat.S_ISREG(metadata.st_mode):
            evidence.append(name)
        elif name in INSTANCE_DIRECTORIES and stat.S_ISDIR(metadata.st_mode):
            evidence.append(name)
    return tuple(evidence)


def _safe_directory(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return stat.S_ISDIR(metadata.st_mode) and not _is_reparse(metadata)


def _is_reparse(metadata: os.stat_result) -> bool:
    if stat.S_ISLNK(metadata.st_mode):
        return True
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(reparse_flag and attributes & reparse_flag)


def _identity(path: Path) -> str:
    try:
        resolved = path.resolve(strict=False)
    except (OSError, RuntimeError):
        resolved = path.absolute()
    return os.path.normcase(str(resolved))
