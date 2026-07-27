from __future__ import annotations

import os
import stat
from collections.abc import Collection
from pathlib import Path, PurePosixPath, PureWindowsPath


class UnsafePathError(ValueError):
    """Raised when an untrusted path cannot be contained safely."""


def parse_untrusted_relative_path(value: object, *, label: str = "path") -> Path:
    if not isinstance(value, str):
        raise UnsafePathError(f"{label} must be a string")
    if not value:
        raise UnsafePathError(f"{label} must not be empty")
    if "\x00" in value:
        raise UnsafePathError(f"{label} contains a NUL character")
    if "\\" in value:
        raise UnsafePathError(f"{label} must use forward slashes")

    windows_path = PureWindowsPath(value)
    posix_path = PurePosixPath(value)
    if windows_path.drive or windows_path.root or windows_path.is_absolute():
        raise UnsafePathError(f"{label} must not be a Windows absolute or drive path")
    if posix_path.is_absolute():
        raise UnsafePathError(f"{label} must not be an absolute path")

    parts = value.split("/")
    # Percent-encoded segments are literal filesystem names here. If URL
    # decoding is ever introduced, validate the decoded value again.
    if any(part == "" for part in parts):
        raise UnsafePathError(f"{label} contains an empty path segment")
    if any(part in {".", ".."} for part in parts):
        raise UnsafePathError(f"{label} contains a dot path segment")

    relative = Path(*parts)
    if relative.is_absolute() or not relative.parts:
        raise UnsafePathError(f"{label} must be a non-empty relative path")
    return relative


def resolve_path_within_root(
    root: Path,
    value: object,
    *,
    label: str = "path",
    allowed_top_levels: Collection[str] | None = None,
) -> Path:
    relative = parse_untrusted_relative_path(value, label=label)
    if allowed_top_levels is not None and not _is_allowed_top_level(
        relative.parts[0], allowed_top_levels
    ):
        allowed = ", ".join(sorted(allowed_top_levels))
        raise UnsafePathError(f"{label} must start with one of: {allowed}")

    resolved_root = Path(root).resolve(strict=False)
    candidate = resolved_root / relative
    resolved_candidate = candidate.resolve(strict=False)
    if not resolved_candidate.is_relative_to(resolved_root):
        raise UnsafePathError(f"{label} resolves outside its designated root")
    return resolved_candidate


def resolve_path_for_operation(
    root: Path,
    value: object,
    *,
    label: str = "path",
    allowed_top_levels: Collection[str] | None = None,
) -> Path:
    # Cross-platform best effort: re-check containment and existing link/reparse
    # components immediately before I/O. This narrows but cannot eliminate every
    # kernel-level race without platform-specific directory-handle operations.
    relative = parse_untrusted_relative_path(value, label=label)
    resolved_root = Path(root).resolve(strict=False)
    candidate = resolved_root / relative
    _reject_existing_reparse_points(resolved_root, relative, label=label)
    resolved_candidate = candidate.resolve(strict=False)
    if not resolved_candidate.is_relative_to(resolved_root):
        raise UnsafePathError(f"{label} resolves outside its designated root")
    if allowed_top_levels is not None and not _is_allowed_top_level(
        relative.parts[0], allowed_top_levels
    ):
        allowed = ", ".join(sorted(allowed_top_levels))
        raise UnsafePathError(f"{label} must start with one of: {allowed}")
    return resolved_candidate


def path_identity_key(path: Path) -> str:
    return os.path.normcase(str(Path(path).resolve(strict=False)))


def _is_allowed_top_level(value: str, allowed: Collection[str]) -> bool:
    normalized = os.path.normcase(value)
    return any(normalized == os.path.normcase(item) for item in allowed)


def _reject_existing_reparse_points(root: Path, relative: Path, *, label: str) -> None:
    current = root
    for part in relative.parts:
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode):
            raise UnsafePathError(f"{label} traverses an existing symbolic link")
        attributes = getattr(metadata, "st_file_attributes", 0)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        if reparse_flag and attributes & reparse_flag:
            raise UnsafePathError(f"{label} traverses an existing Windows reparse point")
