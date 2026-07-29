from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from mc_han.settings import config_path
from mc_han.utils.atomic_json import write_json_atomic
from mc_han.workflow.models import ModpackInspection


PROJECTS_SCHEMA_VERSION = 1
DEFAULT_MAX_RECENT_PROJECTS = 20


@dataclass(frozen=True)
class RecentProject:
    path: Path
    display_name: str
    minecraft_version: str = "unknown"
    loader_name: str = "unknown"
    loader_version: str = "unknown"
    last_opened: str = ""
    current_stage: str = "welcome"
    last_page: str = "home"
    installed: bool = False
    can_rollback: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", Path(self.path))
        for field_name in (
            "display_name",
            "minecraft_version",
            "loader_name",
            "loader_version",
            "last_opened",
            "current_stage",
            "last_page",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str):
                raise TypeError(f"{field_name} must be a string")
            if "\x00" in value or any(
                character in value for character in ("\r", "\n", "\t")
            ):
                raise ValueError(f"{field_name} contains unsafe control characters")
        if type(self.installed) is not bool:
            raise TypeError("installed must be bool")
        if type(self.can_rollback) is not bool:
            raise TypeError("can_rollback must be bool")

    @classmethod
    def from_inspection(
        cls,
        inspection: ModpackInspection,
        *,
        current_stage: str,
        last_page: str,
        previous: RecentProject | None = None,
        opened_at: datetime | None = None,
    ) -> RecentProject:
        now = opened_at or datetime.now(UTC)
        return cls(
            path=inspection.input_directory,
            display_name=inspection.display_name,
            minecraft_version=inspection.minecraft_version,
            loader_name=inspection.loader_name,
            loader_version=inspection.loader_version,
            last_opened=now.isoformat(),
            current_stage=current_stage,
            last_page=last_page,
            installed=previous.installed if previous else False,
            can_rollback=previous.can_rollback if previous else False,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "display_name": self.display_name,
            "minecraft_version": self.minecraft_version,
            "loader_name": self.loader_name,
            "loader_version": self.loader_version,
            "last_opened": self.last_opened,
            "current_stage": self.current_stage,
            "last_page": self.last_page,
            "installed": self.installed,
            "can_rollback": self.can_rollback,
        }

    @classmethod
    def from_dict(cls, raw: object) -> RecentProject | None:
        if not isinstance(raw, dict):
            return None
        path = _clean_path(raw.get("path"))
        display_name = _clean_text(raw.get("display_name"))
        if path is None or display_name is None:
            return None
        try:
            return cls(
                path=path,
                display_name=display_name,
                minecraft_version=_clean_text(
                    raw.get("minecraft_version")
                )
                or "unknown",
                loader_name=_clean_text(raw.get("loader_name")) or "unknown",
                loader_version=_clean_text(raw.get("loader_version"))
                or "unknown",
                last_opened=_clean_text(raw.get("last_opened")) or "",
                current_stage=_clean_text(raw.get("current_stage")) or "welcome",
                last_page=_clean_text(raw.get("last_page")) or "home",
                installed=raw.get("installed") is True,
                can_rollback=raw.get("can_rollback") is True,
            )
        except (TypeError, ValueError):
            return None


@dataclass(frozen=True)
class RecentProjectsSnapshot:
    projects: tuple[RecentProject, ...] = ()
    last_directory: Path | None = None

    def __post_init__(self) -> None:
        projects = tuple(self.projects)
        if any(not isinstance(item, RecentProject) for item in projects):
            raise TypeError("projects must contain RecentProject values")
        object.__setattr__(self, "projects", projects)
        if self.last_directory is not None:
            object.__setattr__(self, "last_directory", Path(self.last_directory))

    @property
    def most_recent(self) -> RecentProject | None:
        return self.projects[0] if self.projects else None


class RecentProjectsStore:
    def __init__(
        self,
        path: Path | None = None,
        *,
        max_projects: int = DEFAULT_MAX_RECENT_PROJECTS,
    ) -> None:
        if type(max_projects) is not int or max_projects <= 0:
            raise ValueError("max_projects must be a positive integer")
        self.path = Path(path) if path is not None else projects_path()
        self.max_projects = max_projects

    def load(self) -> RecentProjectsSnapshot:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return RecentProjectsSnapshot()
        except (OSError, json.JSONDecodeError, UnicodeError):
            return RecentProjectsSnapshot()
        if not isinstance(raw, dict):
            return RecentProjectsSnapshot()
        projects_raw = raw.get("projects")
        projects: list[RecentProject] = []
        if isinstance(projects_raw, list):
            for item in projects_raw:
                project = RecentProject.from_dict(item)
                if project is not None:
                    projects.append(project)
        projects.sort(key=lambda item: item.last_opened, reverse=True)
        last_directory = _clean_path(raw.get("last_directory"))
        return RecentProjectsSnapshot(
            projects=tuple(projects[: self.max_projects]),
            last_directory=last_directory,
        )

    def save(self, snapshot: RecentProjectsSnapshot) -> None:
        payload = {
            "schema_version": PROJECTS_SCHEMA_VERSION,
            "last_directory": (
                str(snapshot.last_directory)
                if snapshot.last_directory is not None
                else ""
            ),
            "projects": [project.to_dict() for project in snapshot.projects],
        }
        write_json_atomic(self.path, payload)

    def upsert(
        self,
        project: RecentProject,
        *,
        last_directory: Path | None = None,
    ) -> RecentProjectsSnapshot:
        snapshot = self.load()
        identity = _path_identity(project.path)
        projects = [
            item
            for item in snapshot.projects
            if _path_identity(item.path) != identity
        ]
        projects.insert(0, project)
        updated = RecentProjectsSnapshot(
            projects=tuple(projects[: self.max_projects]),
            last_directory=last_directory or project.path,
        )
        self.save(updated)
        return updated

    def update_progress(
        self,
        path: Path,
        *,
        current_stage: str,
        last_page: str,
        installed: bool | None = None,
        can_rollback: bool | None = None,
    ) -> RecentProjectsSnapshot:
        snapshot = self.load()
        identity = _path_identity(path)
        projects: list[RecentProject] = []
        for project in snapshot.projects:
            if _path_identity(project.path) != identity:
                projects.append(project)
                continue
            projects.append(
                replace(
                    project,
                    current_stage=current_stage,
                    last_page=last_page,
                    installed=(
                        project.installed if installed is None else installed
                    ),
                    can_rollback=(
                        project.can_rollback
                        if can_rollback is None
                        else can_rollback
                    ),
                )
            )
        updated = RecentProjectsSnapshot(
            projects=tuple(projects),
            last_directory=snapshot.last_directory,
        )
        self.save(updated)
        return updated

    def find(self, path: Path) -> RecentProject | None:
        identity = _path_identity(path)
        return next(
            (
                project
                for project in self.load().projects
                if _path_identity(project.path) == identity
            ),
            None,
        )


class MemoryRecentProjectsStore(RecentProjectsStore):
    """Non-persistent store for embedded windows and tests."""

    def __init__(self, *, max_projects: int = DEFAULT_MAX_RECENT_PROJECTS) -> None:
        if type(max_projects) is not int or max_projects <= 0:
            raise ValueError("max_projects must be a positive integer")
        self.path = Path()
        self.max_projects = max_projects
        self._snapshot = RecentProjectsSnapshot()

    def load(self) -> RecentProjectsSnapshot:
        return self._snapshot

    def save(self, snapshot: RecentProjectsSnapshot) -> None:
        self._snapshot = snapshot


def projects_path() -> Path:
    return config_path().parent / "projects.json"


def _clean_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped or "\x00" in stripped:
        return None
    if any(character in stripped for character in ("\r", "\n", "\t")):
        return None
    return stripped


def _clean_path(value: object) -> Path | None:
    cleaned = _clean_text(value)
    return Path(cleaned) if cleaned is not None else None


def _path_identity(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))
