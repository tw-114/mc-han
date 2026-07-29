from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import tomllib


PACKAGE_NAME = "mc-han"
UNKNOWN_VERSION = "0+unknown"


def get_version() -> str:
    """Return installed metadata version, with a source-tree development fallback."""
    try:
        return version(PACKAGE_NAME)
    except PackageNotFoundError:
        return _source_tree_version()


def _source_tree_version() -> str:
    # Metadata fallback must not depend on resolving unrelated user input paths.
    for parent in Path(__file__).parent.parents:
        pyproject_path = parent / "pyproject.toml"
        if not pyproject_path.is_file():
            continue
        try:
            project = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))["project"]
            source_version = project["version"]
        except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError, UnicodeError):
            return UNKNOWN_VERSION
        if type(source_version) is str and source_version:
            return source_version
        return UNKNOWN_VERSION
    return UNKNOWN_VERSION
