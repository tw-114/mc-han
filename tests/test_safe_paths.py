from __future__ import annotations

from pathlib import Path

import pytest

from mc_han.utils.safe_paths import (
    UnsafePathError,
    parse_untrusted_relative_path,
    resolve_path_within_root,
)


@pytest.mark.parametrize(
    "value",
    [
        "",
        ".",
        "..",
        "assets/../../../escape/ae2guide/page.md",
        "assets/../../escape/lang/en_us.json",
        r"assets\..\..\escape\lang\en_us.json",
        r"C:\outside.txt",
        "C:/outside.txt",
        r"\\server\share\outside.txt",
        "/absolute/path",
        "assets/demo/\x00page.md",
        "assets//demo/page.md",
    ],
)
def test_parse_untrusted_relative_path_rejects_malicious_inputs(value: str):
    with pytest.raises(UnsafePathError):
        parse_untrusted_relative_path(value)


@pytest.mark.parametrize(
    "value",
    [
        "assets/demo/lang/en_us.json",
        "assets/demo/ae2guide/page.md",
        "assets/%2e%2e/file",
    ],
)
def test_parse_untrusted_relative_path_accepts_normal_jar_entries(value: str):
    assert parse_untrusted_relative_path(value).as_posix() == value


def test_resolve_path_within_root_rejects_existing_symlink_escape(tmp_path: Path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    link = root / "assets"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"Directory symlinks are unavailable: {error}")

    with pytest.raises(UnsafePathError, match="outside"):
        resolve_path_within_root(root, "assets/demo/page.md")
