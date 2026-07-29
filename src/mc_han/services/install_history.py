from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from mc_han.builder.installer import (
    InstallResult,
    RollbackResult,
    latest_backup_dir,
    validate_rollback_manifest,
)
from mc_han.core.project import project_paths
from mc_han.utils.atomic_json import write_json_atomic
from mc_han.utils.safe_paths import (
    UnsafePathError,
    parse_untrusted_relative_path,
    resolve_path_for_operation,
)


INSTALL_HISTORY_SCHEMA_VERSION = 1
INSTALL_TOP_LEVELS = {"resourcepacks", "config"}


@dataclass(frozen=True)
class InstallHistoryEntry:
    install_id: str
    installed_at: str
    generated_files: tuple[str, ...]
    overwritten_files: tuple[str, ...]
    backup_relative: str
    manifest_relative: str
    result: str = "installed"
    rolled_back_at: str = ""

    def __post_init__(self) -> None:
        for field_name in (
            "install_id",
            "installed_at",
            "backup_relative",
            "manifest_relative",
            "result",
            "rolled_back_at",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str):
                raise TypeError(f"{field_name} must be a string")
            if "\x00" in value or any(
                character in value for character in ("\r", "\n", "\t")
            ):
                raise ValueError(f"{field_name} contains control characters")
        generated = _stable_targets(self.generated_files, "generated_files")
        overwritten = _stable_targets(
            self.overwritten_files,
            "overwritten_files",
        )
        if set(generated) & set(overwritten):
            raise ValueError("generated_files and overwritten_files overlap")
        object.__setattr__(self, "generated_files", generated)
        object.__setattr__(self, "overwritten_files", overwritten)
        _validate_backup_relative(self.backup_relative)
        manifest = parse_untrusted_relative_path(
            self.manifest_relative,
            label="manifest_relative",
        )
        if manifest.name != "install_manifest.json":
            raise ValueError("manifest_relative must name install_manifest.json")
        if manifest.parent.as_posix() != self.backup_relative:
            raise ValueError("manifest_relative must be inside backup_relative")
        if self.result not in {"installed", "rolled_back"}:
            raise ValueError("result must be installed or rolled_back")
        if self.result == "rolled_back" and not self.rolled_back_at:
            raise ValueError("rolled_back_at is required for rolled_back result")

    @property
    def installed_files(self) -> int:
        return len(self.generated_files) + len(self.overwritten_files)

    @property
    def backed_up_files(self) -> int:
        return len(self.overwritten_files)

    def to_dict(self) -> dict[str, object]:
        return {
            "install_id": self.install_id,
            "installed_at": self.installed_at,
            "generated_files": list(self.generated_files),
            "overwritten_files": list(self.overwritten_files),
            "backup_relative": self.backup_relative,
            "manifest_relative": self.manifest_relative,
            "result": self.result,
            "rolled_back_at": self.rolled_back_at,
        }

    @classmethod
    def from_dict(cls, raw: object) -> InstallHistoryEntry | None:
        if not isinstance(raw, dict):
            return None
        try:
            return cls(
                install_id=raw.get("install_id", ""),
                installed_at=raw.get("installed_at", ""),
                generated_files=tuple(raw.get("generated_files", ())),
                overwritten_files=tuple(raw.get("overwritten_files", ())),
                backup_relative=raw.get("backup_relative", ""),
                manifest_relative=raw.get("manifest_relative", ""),
                result=raw.get("result", "installed"),
                rolled_back_at=raw.get("rolled_back_at", ""),
            )
        except (TypeError, ValueError, UnsafePathError):
            return None


class InstallHistoryStore:
    def __init__(self, modpack_dir: Path) -> None:
        self.modpack_dir = Path(modpack_dir).resolve(strict=False)
        self.path = project_paths(self.modpack_dir).install_history_json

    def load(self) -> tuple[InstallHistoryEntry, ...]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return ()
        except (OSError, UnicodeError, json.JSONDecodeError):
            return ()
        if not isinstance(raw, dict) or not isinstance(
            raw.get("entries"),
            list,
        ):
            return ()
        entries = [
            entry
            for item in raw["entries"]
            if (entry := InstallHistoryEntry.from_dict(item)) is not None
        ]
        return tuple(sorted(entries, key=lambda item: item.installed_at))

    def save(self, entries: tuple[InstallHistoryEntry, ...]) -> None:
        write_json_atomic(
            self.path,
            {
                "schema_version": INSTALL_HISTORY_SCHEMA_VERSION,
                "entries": [entry.to_dict() for entry in entries],
            },
        )

    def record_install(self, result: InstallResult) -> InstallHistoryEntry:
        manifest_path = result.manifest_path
        if manifest_path is None:
            raise ValueError("manifest_path is required to record an install")
        manifest, plan = _load_valid_manifest(
            self.modpack_dir,
            result.backup_dir,
        )
        backup_relative = _relative_to_modpack(
            self.modpack_dir,
            result.backup_dir,
            "backup_dir",
        )
        manifest_relative = _relative_to_modpack(
            self.modpack_dir,
            manifest_path,
            "manifest_path",
        )
        created_at = manifest.get("created_at")
        installed_at = (
            created_at
            if isinstance(created_at, str) and _safe_text(created_at)
            else datetime.now(UTC).isoformat()
        )
        entry = InstallHistoryEntry(
            install_id=Path(backup_relative).name,
            installed_at=installed_at,
            generated_files=tuple(
                item.relative_target for item in plan if not item.had_backup
            ),
            overwritten_files=tuple(
                item.relative_target for item in plan if item.had_backup
            ),
            backup_relative=backup_relative,
            manifest_relative=manifest_relative,
        )
        entries = tuple(
            item
            for item in self.load()
            if item.install_id != entry.install_id
        )
        self.save((*entries, entry))
        return entry

    def mark_rolled_back(
        self,
        result: RollbackResult,
    ) -> InstallHistoryEntry | None:
        backup_relative = _relative_to_modpack(
            self.modpack_dir,
            result.backup_dir,
            "backup_dir",
        )
        entries = list(self.load())
        matched: InstallHistoryEntry | None = None
        for index, entry in enumerate(entries):
            if entry.backup_relative != backup_relative:
                continue
            matched = InstallHistoryEntry(
                **{
                    **entry.to_dict(),
                    "generated_files": entry.generated_files,
                    "overwritten_files": entry.overwritten_files,
                    "result": "rolled_back",
                    "rolled_back_at": datetime.now(UTC).isoformat(),
                }
            )
            entries[index] = matched
            break
        if matched is not None:
            self.save(tuple(entries))
        return matched

    def latest_available(self) -> InstallHistoryEntry | None:
        for entry in reversed(self.load()):
            if entry.result != "installed":
                continue
            try:
                backup_dir = resolve_history_backup(
                    self.modpack_dir,
                    entry,
                )
            except (UnsafePathError, ValueError, RuntimeError):
                continue
            if (backup_dir / "rollback_report.txt").exists():
                continue
            try:
                _load_valid_manifest(self.modpack_dir, backup_dir)
            except (OSError, ValueError, RuntimeError, json.JSONDecodeError):
                continue
            return entry
        return None


def recover_latest_install_result(modpack_dir: Path) -> InstallResult | None:
    modpack_dir = Path(modpack_dir).resolve(strict=False)
    store = InstallHistoryStore(modpack_dir)
    entry = store.latest_available()
    if entry is not None:
        backup_dir = resolve_history_backup(modpack_dir, entry)
        manifest_path = resolve_path_for_operation(
            modpack_dir,
            entry.manifest_relative,
            label="install history manifest",
            allowed_top_levels={".mc-han"},
        )
        return InstallResult(
            installed_files=entry.installed_files,
            backed_up_files=entry.backed_up_files,
            backup_dir=backup_dir,
            manifest_path=manifest_path,
        )

    # Old releases did not write install_history.json. Their validated manifest
    # remains sufficient to restore the latest installation.
    try:
        backup_dir = latest_backup_dir(modpack_dir)
        if (backup_dir / "rollback_report.txt").exists():
            return None
        _manifest, plan = _load_valid_manifest(modpack_dir, backup_dir)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError):
        return None
    return InstallResult(
        installed_files=len(plan),
        backed_up_files=sum(item.had_backup for item in plan),
        backup_dir=backup_dir,
        manifest_path=backup_dir / "install_manifest.json",
    )


def resolve_history_backup(
    modpack_dir: Path,
    entry: InstallHistoryEntry,
) -> Path:
    backup_dir = resolve_path_for_operation(
        modpack_dir,
        entry.backup_relative,
        label="install history backup",
        allowed_top_levels={".mc-han"},
    )
    backups_root = (
        Path(modpack_dir).resolve(strict=False) / ".mc-han" / "backups"
    ).resolve(strict=False)
    if not backup_dir.is_relative_to(backups_root):
        raise UnsafePathError(
            "install history backup must remain under .mc-han/backups"
        )
    return backup_dir


def _load_valid_manifest(
    modpack_dir: Path,
    backup_dir: Path,
) -> tuple[dict[str, object], list]:
    backup_relative = _relative_to_modpack(
        modpack_dir,
        backup_dir,
        "backup_dir",
    )
    _validate_backup_relative(backup_relative)
    safe_backup = resolve_path_for_operation(
        modpack_dir,
        backup_relative,
        label="install backup",
        allowed_top_levels={".mc-han"},
    )
    manifest_path = resolve_path_for_operation(
        safe_backup,
        "install_manifest.json",
        label="install manifest",
    )
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    plan = validate_rollback_manifest(
        raw,
        modpack_dir=modpack_dir,
        backup_dir=safe_backup,
    )
    return raw, plan


def _relative_to_modpack(
    modpack_dir: Path,
    path: Path,
    label: str,
) -> str:
    root = Path(modpack_dir).resolve(strict=False)
    resolved = Path(path).resolve(strict=False)
    if not resolved.is_relative_to(root):
        raise UnsafePathError(f"{label} must remain inside the modpack")
    relative = resolved.relative_to(root).as_posix()
    parse_untrusted_relative_path(relative, label=label)
    return relative


def _validate_backup_relative(value: str) -> None:
    relative = parse_untrusted_relative_path(
        value,
        label="backup_relative",
    )
    parts = relative.parts
    if len(parts) < 3 or parts[:2] != (".mc-han", "backups"):
        raise ValueError("backup_relative must be under .mc-han/backups")


def _stable_targets(values: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)):
        raise TypeError(f"{field_name} must be a sequence")
    normalized: list[str] = []
    for value in values:
        relative = parse_untrusted_relative_path(value, label=field_name)
        if relative.parts[0] not in INSTALL_TOP_LEVELS:
            raise ValueError(
                f"{field_name} must use resourcepacks or config targets"
            )
        normalized.append(relative.as_posix())
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field_name} contains duplicate targets")
    return tuple(sorted(normalized))


def _safe_text(value: str) -> bool:
    return bool(value) and "\x00" not in value and not any(
        character in value for character in ("\r", "\n", "\t")
    )
