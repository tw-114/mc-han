from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from PySide6.QtCore import QEvent, QObject, QTimer, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QWidget


class ThemePreference(str, Enum):
    SYSTEM = "system"
    LIGHT = "light"
    DARK = "dark"


class EffectiveTheme(str, Enum):
    LIGHT = "light"
    DARK = "dark"


@dataclass(frozen=True)
class ThemePalette:
    background: str
    surface: str
    elevated: str
    primary: str
    primary_hover: str
    primary_text: str
    success: str
    warning: str
    error: str
    text: str
    muted: str
    border: str
    control: str
    control_hover: str
    disabled_text: str
    disabled_surface: str
    scroll_handle: str
    primary_soft: str
    success_soft: str
    warning_soft: str
    error_soft: str
    neutral_soft: str


LIGHT_PALETTE = ThemePalette(
    background="#F5F7FA",
    surface="#FFFFFF",
    elevated="#FFFFFF",
    primary="#2F80ED",
    primary_hover="#216FD1",
    primary_text="#FFFFFF",
    success="#248950",
    warning="#A96908",
    error="#B83C38",
    text="#252A34",
    muted="#6F7785",
    border="#D8DDE6",
    control="#FFFFFF",
    control_hover="#F7F9FC",
    disabled_text="#929AA8",
    disabled_surface="#EEF1F5",
    scroll_handle="#C8CED8",
    primary_soft="#EAF3FF",
    success_soft="#EAF8F0",
    warning_soft="#FFF5E5",
    error_soft="#FDEDEC",
    neutral_soft="#F1F3F6",
)

DARK_PALETTE = ThemePalette(
    background="#15181E",
    surface="#20242C",
    elevated="#272C35",
    primary="#5AA2FF",
    primary_hover="#7CB6FF",
    primary_text="#0D1117",
    success="#5CCB8A",
    warning="#F2B84B",
    error="#FF7470",
    text="#F3F5F8",
    muted="#AAB2C0",
    border="#3A414D",
    control="#262B34",
    control_hover="#303640",
    disabled_text="#737C8A",
    disabled_surface="#292E37",
    scroll_handle="#596272",
    primary_soft="#1B3556",
    success_soft="#173D2A",
    warning_soft="#473716",
    error_soft="#4A2527",
    neutral_soft="#303640",
)

SystemThemeDetector = Callable[[], EffectiveTheme]
_ACTIVE_PALETTE = LIGHT_PALETTE


class ThemeManager(QObject):
    changed = Signal(object)

    def __init__(
        self,
        application: QApplication,
        *,
        preference: ThemePreference = ThemePreference.SYSTEM,
        system_detector: SystemThemeDetector | None = None,
    ) -> None:
        super().__init__(application)
        if not isinstance(preference, ThemePreference):
            raise TypeError("preference must be ThemePreference")
        self.application = application
        self.preference = preference
        self.system_detector = system_detector or detect_system_theme
        self.effective_theme = self._resolve()
        self.application.installEventFilter(self)

    @property
    def palette(self) -> ThemePalette:
        return (
            DARK_PALETTE
            if self.effective_theme is EffectiveTheme.DARK
            else LIGHT_PALETTE
        )

    def set_preference(self, preference: ThemePreference) -> None:
        if not isinstance(preference, ThemePreference):
            raise TypeError("preference must be ThemePreference")
        changed = preference is not self.preference
        self.preference = preference
        self._apply_if_changed(force=changed)

    def refresh_system_theme(self) -> None:
        if self.preference is ThemePreference.SYSTEM:
            self._apply_if_changed()

    def apply(self, window: QWidget | None = None) -> None:
        global _ACTIVE_PALETTE
        _ACTIVE_PALETTE = self.palette
        theme_changed = (
            self.application.property("mcHanTheme")
            != self.effective_theme.value
        )
        self.application.setProperty("mcHanTheme", self.effective_theme.value)
        if theme_changed or not self.application.styleSheet():
            self.application.setStyleSheet(
                application_stylesheet(self.palette)
            )
            qt_palette = self.application.palette()
            qt_palette.setColor(
                QPalette.ColorRole.Window,
                QColor(self.palette.background),
            )
            qt_palette.setColor(
                QPalette.ColorRole.WindowText,
                QColor(self.palette.text),
            )
            qt_palette.setColor(
                QPalette.ColorRole.Base,
                QColor(self.palette.control),
            )
            qt_palette.setColor(
                QPalette.ColorRole.Text,
                QColor(self.palette.text),
            )
            qt_palette.setColor(
                QPalette.ColorRole.Button,
                QColor(self.palette.control),
            )
            qt_palette.setColor(
                QPalette.ColorRole.ButtonText,
                QColor(self.palette.text),
            )
            qt_palette.setColor(
                QPalette.ColorRole.Highlight,
                QColor(self.palette.primary),
            )
            qt_palette.setColor(
                QPalette.ColorRole.HighlightedText,
                QColor(self.palette.primary_text),
            )
            self.application.setPalette(qt_palette)
        targets = list(self.application.topLevelWidgets())
        if window is not None and window not in targets:
            targets.append(window)
        for target in targets:
            _apply_windows_title_bar(target, self.effective_theme)
        QTimer.singleShot(0, self._reapply_windows_title_bars)

    def dispose(self) -> None:
        self.application.removeEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() is QEvent.Type.ApplicationActivate:
            self.refresh_system_theme()
        elif (
            event.type() is QEvent.Type.Show
            and isinstance(watched, QWidget)
            and watched.isWindow()
        ):
            QTimer.singleShot(
                0,
                lambda widget=watched: _apply_windows_title_bar(
                    widget,
                    self.effective_theme,
                ),
            )
        return False

    def _resolve(self) -> EffectiveTheme:
        if self.preference is ThemePreference.LIGHT:
            return EffectiveTheme.LIGHT
        if self.preference is ThemePreference.DARK:
            return EffectiveTheme.DARK
        return self.system_detector()

    def _apply_if_changed(self, *, force: bool = False) -> None:
        resolved = self._resolve()
        if not force and resolved is self.effective_theme:
            return
        self.effective_theme = resolved
        self.apply()
        self.changed.emit(resolved)

    def _reapply_windows_title_bars(self) -> None:
        for target in self.application.topLevelWidgets():
            _apply_windows_title_bar(target, self.effective_theme)


def active_palette() -> ThemePalette:
    return _ACTIVE_PALETTE


def detect_system_theme() -> EffectiveTheme:
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            ) as key:
                value, _kind = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return (
                EffectiveTheme.LIGHT
                if int(value) != 0
                else EffectiveTheme.DARK
            )
        except (OSError, TypeError, ValueError):
            pass
    application = QApplication.instance()
    if application is not None:
        color = application.palette().color(QPalette.ColorRole.Window)
        return (
            EffectiveTheme.DARK
            if color.lightness() < 128
            else EffectiveTheme.LIGHT
        )
    return EffectiveTheme.LIGHT


def application_stylesheet(palette: ThemePalette = LIGHT_PALETTE) -> str:
    p = palette
    return f"""
    * {{
        font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
        font-size: 14px;
        color: {p.text};
    }}
    QMainWindow, QDialog, QMessageBox, QWidget#AppRoot, QScrollArea,
    QScrollArea > QWidget > QWidget {{
        background: {p.background};
    }}
    QFrame#TopBar, QFrame#FooterBar {{
        background: {p.surface};
        border: 0;
        border-bottom: 1px solid {p.border};
    }}
    QFrame#FooterBar {{
        border-top: 1px solid {p.border};
        border-bottom: 0;
    }}
    QFrame#Card {{
        background: {p.surface};
        border: 1px solid {p.border};
        border-radius: 12px;
    }}
    QLabel#BrandLabel {{ color: {p.primary}; font-size: 22px; font-weight: 700; }}
    QLabel#PageTitle {{ font-size: 28px; font-weight: 700; }}
    QLabel#SectionTitle {{ font-size: 18px; font-weight: 700; }}
    QLabel#CardTitle {{ font-size: 16px; font-weight: 700; }}
    QLabel#MutedLabel {{ color: {p.muted}; }}
    QLabel#ValueLabel {{ font-size: 16px; font-weight: 600; }}
    QLabel[tone="primary"] {{ color: {p.primary}; }}
    QLabel[tone="success"] {{ color: {p.success}; }}
    QLabel[tone="warning"] {{ color: {p.warning}; }}
    QLabel[tone="error"] {{ color: {p.error}; }}
    QLabel[tone="neutral"] {{ color: {p.muted}; }}
    QLabel#StatusChip {{ padding: 5px 10px; border-radius: 9px; font-weight: 600; }}
    QLabel#StatusChip[tone="primary"] {{ color: {p.primary}; background: {p.primary_soft}; }}
    QLabel#StatusChip[tone="success"] {{ color: {p.success}; background: {p.success_soft}; }}
    QLabel#StatusChip[tone="warning"] {{ color: {p.warning}; background: {p.warning_soft}; }}
    QLabel#StatusChip[tone="error"] {{ color: {p.error}; background: {p.error_soft}; }}
    QLabel#StatusChip[tone="neutral"] {{ color: {p.muted}; background: {p.neutral_soft}; }}
    QPushButton {{
        min-height: 36px; padding: 0 16px; background: {p.control};
        border: 1px solid {p.border}; border-radius: 8px;
    }}
    QPushButton:hover {{ background: {p.control_hover}; border-color: {p.muted}; }}
    QPushButton:disabled {{
        color: {p.disabled_text}; background: {p.disabled_surface};
        border-color: {p.border};
    }}
    QPushButton[variant="primary"] {{
        color: {p.primary_text}; background: {p.primary};
        border-color: {p.primary}; font-weight: 600;
    }}
    QPushButton[variant="primary"]:hover {{
        background: {p.primary_hover}; border-color: {p.primary_hover};
    }}
    QPushButton[nav="true"] {{
        border: 0; background: transparent; color: {p.muted};
        padding: 0 8px; min-height: 34px;
    }}
    QPushButton[nav="true"][completed="true"] {{ color: {p.success}; }}
    QPushButton[nav="true"]:checked {{ color: {p.primary}; background: {p.primary_soft}; }}
    QPushButton[nav="true"]:disabled {{ color: {p.disabled_text}; background: transparent; }}
    QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox,
    QDoubleSpinBox, QTableView, QTreeView, QListView {{
        min-height: 36px; padding: 0 10px; color: {p.text};
        background: {p.control}; border: 1px solid {p.border};
        border-radius: 7px; selection-background-color: {p.primary};
        selection-color: {p.primary_text};
    }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,
    QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
    QTableView:focus {{ border-color: {p.primary}; }}
    QComboBox QAbstractItemView {{
        color: {p.text}; background: {p.elevated};
        selection-background-color: {p.primary_soft};
    }}
    QHeaderView::section {{
        color: {p.text}; background: {p.elevated};
        border: 0; border-right: 1px solid {p.border};
        border-bottom: 1px solid {p.border}; padding: 8px;
    }}
    QComboBox::drop-down, QSpinBox::up-button, QSpinBox::down-button,
    QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
        border: 0; width: 24px;
    }}
    QProgressBar {{
        min-height: 7px; max-height: 7px; border: 0;
        border-radius: 3px; background: {p.border}; text-align: center;
    }}
    QProgressBar::chunk {{ border-radius: 3px; background: {p.primary}; }}
    QCheckBox#CategoryCheckBox {{ font-size: 16px; font-weight: 700; spacing: 10px; }}
    QCheckBox#CategoryCheckBox::indicator {{ width: 18px; height: 18px; }}
    QScrollBar:vertical {{ width: 10px; background: transparent; margin: 2px; }}
    QScrollBar:horizontal {{ height: 10px; background: transparent; margin: 2px; }}
    QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
        min-height: 28px; min-width: 28px; background: {p.scroll_handle};
        border-radius: 4px;
    }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QToolTip {{
        color: {p.text}; background: {p.elevated};
        border: 1px solid {p.border}; padding: 5px;
    }}
    QMenu {{
        color: {p.text}; background: {p.elevated};
        border: 1px solid {p.border};
    }}
    QMenu::item:selected {{ background: {p.primary_soft}; }}
    """


def _apply_windows_title_bar(
    widget: QWidget,
    theme: EffectiveTheme,
) -> None:
    if os.name != "nt" or not widget.isWindow():
        return
    try:
        hwnd = int(widget.winId())
        enabled = ctypes.c_int(1 if theme is EffectiveTheme.DARK else 0)
        dwm = ctypes.windll.dwmapi
        result = dwm.DwmSetWindowAttribute(
            hwnd,
            20,
            ctypes.byref(enabled),
            ctypes.sizeof(enabled),
        )
        if result != 0:
            dwm.DwmSetWindowAttribute(
                hwnd,
                19,
                ctypes.byref(enabled),
                ctypes.sizeof(enabled),
            )
    except (AttributeError, OSError, TypeError, ValueError):
        return
