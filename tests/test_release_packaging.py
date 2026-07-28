from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

import mc_han
from mc_han.cli import build_parser
from mc_han.release_info import (
    about_text,
    release_tag,
    windows_archive_name,
    windows_checksum_name,
)
from mc_han.version import get_version


ROOT = Path(__file__).resolve().parents[1]


def project_version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]["version"]


def test_package_runtime_and_release_names_use_one_version_source():
    expected = project_version()

    assert expected == "0.7.0b1"
    assert mc_han.__version__ == expected
    assert get_version() == expected
    assert release_tag() == "v0.7.0-beta.1"
    assert windows_archive_name() == "mc-han-windows-x64-v0.7.0-beta.1.zip"
    assert windows_checksum_name() == (
        "mc-han-windows-x64-v0.7.0-beta.1.sha256"
    )
    assert expected in about_text()


def test_cli_version_uses_runtime_version(capsys):
    with pytest.raises(SystemExit) as exit_info:
        build_parser().parse_args(["--version"])

    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"mc-han {get_version()}"


def test_pyinstaller_spec_is_onedir_windowed_and_scoped():
    spec = (ROOT / "packaging" / "mc-han-qt.spec").read_text(encoding="utf-8")

    assert 'name="mc-han"' in spec
    assert "console=False" in spec
    assert "COLLECT(" in spec
    assert "collect_all" not in spec
    assert "PySide6.QtCore" in spec
    assert "PySide6.QtGui" in spec
    assert "PySide6.QtWidgets" in spec
    assert "tests" in spec
    assert "tkinter" in spec


def test_windows_build_script_uses_derived_archive_name_and_audits_output():
    script = (ROOT / "scripts" / "build_windows.ps1").read_text(encoding="utf-8")

    assert "windows_archive_name" in script
    assert '"pytest"' in script
    assert '"--basetemp"' in script
    assert '"compileall", "-q", "src"' in script
    assert "qwindows.dll" in script
    assert "Prune-UnusedQtComponents" in script
    assert "Qt6Qml.dll" in script
    assert "Qt6Quick.dll" in script
    assert "THIRD_PARTY_NOTICES.txt" in script
    assert '"licenses"' in script
    assert "Get-FileHash" in script
    assert "windows_checksum_name" in script
    assert "CheckEndToEnd" in script
    assert re.search(r"translations\.sqlite", script)


def test_release_notes_and_notices_describe_preview_boundary():
    notes = (
        ROOT / "release-notes" / "v0.7.0-beta.1.md"
    ).read_text(encoding="utf-8")
    notices = (ROOT / "THIRD_PARTY_NOTICES.txt").read_text(encoding="utf-8")

    assert "预览版" in notes
    assert "建议先备份整合包" in notes
    assert "不会修改 `mods/*.jar`" in notes
    assert "PySide6" in notices
    assert "Shiboken6" in notices
    assert "PyInstaller" in notices
    assert "Qt" in notices
