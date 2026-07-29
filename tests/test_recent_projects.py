from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mc_han.services.recent_projects import (
    RecentProject,
    RecentProjectsSnapshot,
    RecentProjectsStore,
)


def project(path: Path, name: str, opened: str) -> RecentProject:
    return RecentProject(
        path=path,
        display_name=name,
        minecraft_version="1.21.1",
        loader_name="NeoForge",
        loader_version="21.1.172",
        last_opened=opened,
        current_stage="scan_result",
        last_page="scan",
    )


def test_recent_projects_round_trip_and_most_recent_first(tmp_path: Path):
    store = RecentProjectsStore(tmp_path / "projects.json")
    older = project(tmp_path / "Older", "Older", "2026-01-01T00:00:00+00:00")
    newer = project(tmp_path / "Newer", "Newer", "2026-02-01T00:00:00+00:00")

    store.save(
        RecentProjectsSnapshot(
            projects=(older, newer),
            last_directory=tmp_path,
        )
    )
    loaded = store.load()

    assert loaded.projects == (newer, older)
    assert loaded.most_recent == newer
    assert loaded.last_directory == tmp_path
    assert json.loads(store.path.read_text(encoding="utf-8"))[
        "schema_version"
    ] == 1


def test_upsert_preserves_install_state_and_updates_progress(tmp_path: Path):
    store = RecentProjectsStore(tmp_path / "projects.json")
    original = RecentProject(
        path=tmp_path / "Pack",
        display_name="Pack",
        installed=True,
        can_rollback=True,
        last_opened="2026-01-01T00:00:00+00:00",
    )
    store.upsert(original)

    updated = store.update_progress(
        original.path,
        current_stage="translation_review",
        last_page="translation_review",
    )

    assert updated.projects[0].installed
    assert updated.projects[0].can_rollback
    assert updated.projects[0].current_stage == "translation_review"
    assert updated.projects[0].last_page == "translation_review"


def test_atomic_replace_failure_preserves_previous_projects_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    store = RecentProjectsStore(tmp_path / "projects.json")
    store.upsert(
        project(
            tmp_path / "Pack",
            "Pack",
            datetime.now(UTC).isoformat(),
        )
    )
    previous = store.path.read_bytes()

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr("mc_han.services.recent_projects.os.replace", fail_replace)

    with pytest.raises(OSError):
        store.upsert(
            project(
                tmp_path / "Another",
                "Another",
                datetime.now(UTC).isoformat(),
            )
        )

    assert store.path.read_bytes() == previous
    assert list(tmp_path.glob("projects.*.tmp")) == []


def test_invalid_or_control_character_entries_are_ignored(tmp_path: Path):
    path = tmp_path / "projects.json"
    path.write_text(
        json.dumps(
            {
                "projects": [
                    {"path": str(tmp_path / "ok"), "display_name": "正常项目"},
                    {
                        "path": str(tmp_path / "bad"),
                        "display_name": "bad\nname",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    loaded = RecentProjectsStore(path).load()

    assert [item.display_name for item in loaded.projects] == ["正常项目"]
