from __future__ import annotations

from pathlib import Path


def default_ftbquests_overlay_dir(output_dir: Path) -> Path:
    return Path(output_dir) / "config" / "ftbquests" / "quests"
