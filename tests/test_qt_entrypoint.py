from __future__ import annotations

import importlib

from mc_han import qt_app


def test_cli_import_does_not_require_pyside6():
    module = importlib.import_module("mc_han.cli")

    assert callable(module.main)


def test_qt_entrypoint_has_concise_missing_dependency_message(monkeypatch, capsys):
    monkeypatch.setattr(qt_app.importlib.util, "find_spec", lambda name: None)

    exit_code = qt_app.main()
    captured = capsys.readouterr()

    assert exit_code == 2
    assert captured.out == ""
    assert 'python -m pip install -e ".[qt]"' in captured.err
    assert "Traceback" not in captured.err
