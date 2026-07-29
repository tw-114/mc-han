from __future__ import annotations

import os
from pathlib import Path

import pytest

from mc_han.services.project_discovery import (
    DiscoveryLocation,
    discover_modpacks,
)


def test_discovers_launcher_instances_without_reading_jar_contents(
    tmp_path: Path,
):
    prism_root = tmp_path / "Prism" / "instances"
    prism_game = prism_root / "Demo" / ".minecraft"
    (prism_game / "mods").mkdir(parents=True)
    (prism_game / "mods" / "demo.jar").write_bytes(b"not opened")

    curse_root = tmp_path / "Curse" / "Instances"
    (curse_root / "ATM10" / "config").mkdir(parents=True)

    discovered = discover_modpacks(
        locations=(
            DiscoveryLocation("Prism Launcher", prism_root),
            DiscoveryLocation("CurseForge", curse_root),
        )
    )

    assert [(item.display_name, item.launcher) for item in discovered] == [
        ("ATM10", "CurseForge"),
        ("Demo", "Prism Launcher"),
    ]
    assert next(item for item in discovered if item.display_name == "Demo").path == (
        prism_game
    )


def test_manual_paths_are_deduplicated_against_launcher_results(
    tmp_path: Path,
):
    root = tmp_path / "instances"
    pack = root / "Pack"
    (pack / "mods").mkdir(parents=True)

    discovered = discover_modpacks(
        locations=(DiscoveryLocation("PCL2", root),),
        manual_paths=(pack, pack),
    )

    assert len(discovered) == 1
    assert discovered[0].launcher == "PCL2"


def test_empty_and_unreadable_roots_are_ignored(tmp_path: Path):
    empty = tmp_path / "empty"
    empty.mkdir()

    assert discover_modpacks(
        locations=(
            DiscoveryLocation("Empty", empty),
            DiscoveryLocation("Missing", tmp_path / "missing"),
        )
    ) == ()


def test_external_directory_symlink_is_not_discovered(tmp_path: Path):
    root = tmp_path / "instances"
    outside = tmp_path / "outside"
    (outside / "mods").mkdir(parents=True)
    root.mkdir()
    link = root / "linked-pack"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks are not available")

    assert discover_modpacks(
        locations=(DiscoveryLocation("Launcher", root),)
    ) == ()
    assert os.path.exists(outside / "mods")
