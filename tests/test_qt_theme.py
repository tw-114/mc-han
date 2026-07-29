from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from mc_han.qt.main_window import MainWindow
from mc_han.qt.project_session import TaskKind
from mc_han.qt.theme import (
    DARK_PALETTE,
    LIGHT_PALETTE,
    EffectiveTheme,
    ThemeManager,
    ThemePreference,
    application_stylesheet,
)
from mc_han.qt.view_models import WorkflowStage
from mc_han.settings import UserSettings, load_settings, save_settings


@pytest.fixture(scope="module")
def application():
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


def test_default_follows_simulated_light_and_dark_system(
    application: QApplication,
):
    light = ThemeManager(
        application,
        system_detector=lambda: EffectiveTheme.LIGHT,
    )
    light.apply()
    assert light.preference is ThemePreference.SYSTEM
    assert light.effective_theme is EffectiveTheme.LIGHT
    assert application.property("mcHanTheme") == "light"
    light.dispose()

    dark = ThemeManager(
        application,
        system_detector=lambda: EffectiveTheme.DARK,
    )
    dark.apply()
    assert dark.effective_theme is EffectiveTheme.DARK
    assert application.property("mcHanTheme") == "dark"
    assert DARK_PALETTE.background in application.styleSheet()
    dark.dispose()


def test_manual_theme_overrides_system_and_follow_refreshes(
    application: QApplication,
):
    system = {"theme": EffectiveTheme.DARK}
    manager = ThemeManager(
        application,
        system_detector=lambda: system["theme"],
    )
    manager.apply()
    manager.set_preference(ThemePreference.LIGHT)
    system["theme"] = EffectiveTheme.DARK
    manager.refresh_system_theme()
    assert manager.effective_theme is EffectiveTheme.LIGHT

    manager.set_preference(ThemePreference.SYSTEM)
    assert manager.effective_theme is EffectiveTheme.DARK
    system["theme"] = EffectiveTheme.LIGHT
    manager.refresh_system_theme()
    assert manager.effective_theme is EffectiveTheme.LIGHT
    manager.dispose()


def test_theme_preference_persists_and_reopens(tmp_path: Path):
    path = tmp_path / "config.json"
    save_settings(UserSettings(theme_mode="dark"), path)
    assert load_settings(path).theme_mode == "dark"
    save_settings(UserSettings(theme_mode="system"), path)
    assert load_settings(path).theme_mode == "system"


def test_switching_theme_during_task_preserves_page_and_task(
    application: QApplication,
    tmp_path: Path,
):
    settings_path = tmp_path / "config.json"
    manager = ThemeManager(
        application,
        system_detector=lambda: EffectiveTheme.LIGHT,
    )
    window = MainWindow(
        settings_path=settings_path,
        theme_manager=manager,
    )
    window.stage = WorkflowStage.FULL_TRANSLATION
    window._show_page(window.full_translation_page)
    worker = object()
    started, _reason = window.workflow_controller.begin_task(
        TaskKind.FULL_TRANSLATION,
        "完整翻译",
        worker,
    )
    assert started

    window.show_settings()
    window.change_theme_preference("dark")

    assert window.pages.currentWidget() is window.settings_page
    assert window.stage is WorkflowStage.FULL_TRANSLATION
    assert window.session.active_task is not None
    assert window.session.active_task.worker is worker
    window.close_settings()
    assert window.pages.currentWidget() is window.full_translation_page
    assert load_settings(settings_path).theme_mode == "dark"
    window.workflow_controller.finish_task(worker)
    window.close()
    manager.dispose()


def test_application_styles_cover_dialogs_tables_and_all_semantic_states():
    light = application_stylesheet(LIGHT_PALETTE)
    dark = application_stylesheet(DARK_PALETTE)
    for stylesheet in (light, dark):
        assert "QDialog" in stylesheet
        assert "QMessageBox" in stylesheet
        assert "QTableView" in stylesheet
        assert "QToolTip" in stylesheet
        assert "QProgressBar" in stylesheet
        assert 'tone="success"' in stylesheet
        assert 'tone="warning"' in stylesheet
        assert 'tone="error"' in stylesheet
    assert LIGHT_PALETTE.background != DARK_PALETTE.background
    assert LIGHT_PALETTE.text != DARK_PALETTE.text


def test_qt_pages_do_not_hardcode_hex_colors_outside_theme_module():
    qt_root = Path(__file__).parents[1] / "src" / "mc_han" / "qt"
    offenders = []
    for path in qt_root.rglob("*.py"):
        if path.name == "theme.py":
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"#[0-9A-Fa-f]{6}", text):
            offenders.append(path.relative_to(qt_root).as_posix())
    assert offenders == []
