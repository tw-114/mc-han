from __future__ import annotations

COLORS = {
    "background": "#F5F7FA",
    "surface": "#FFFFFF",
    "primary": "#2F80ED",
    "primary_hover": "#216FD1",
    "success": "#2EAD65",
    "warning": "#E6A23C",
    "error": "#D9534F",
    "text": "#252A34",
    "muted": "#6F7785",
    "border": "#E4E8EF",
}


def application_stylesheet() -> str:
    return """
    * {
        font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
        font-size: 14px;
        color: #252A34;
    }
    QMainWindow, QWidget#AppRoot, QScrollArea, QScrollArea > QWidget > QWidget {
        background: #F5F7FA;
    }
    QFrame#TopBar, QFrame#FooterBar {
        background: #FFFFFF;
        border: 0;
        border-bottom: 1px solid #E4E8EF;
    }
    QFrame#FooterBar {
        border-top: 1px solid #E4E8EF;
        border-bottom: 0;
    }
    QFrame#Card {
        background: #FFFFFF;
        border: 1px solid #E4E8EF;
        border-radius: 12px;
    }
    QLabel#BrandLabel {
        color: #2F80ED;
        font-size: 22px;
        font-weight: 700;
    }
    QLabel#PageTitle {
        font-size: 28px;
        font-weight: 700;
    }
    QLabel#SectionTitle {
        font-size: 18px;
        font-weight: 700;
    }
    QLabel#CardTitle {
        font-size: 16px;
        font-weight: 700;
    }
    QLabel#MutedLabel {
        color: #6F7785;
    }
    QLabel#ValueLabel {
        font-size: 16px;
        font-weight: 600;
    }
    QLabel[tone="primary"] {
        color: #2F80ED;
    }
    QLabel[tone="success"] {
        color: #2EAD65;
    }
    QLabel[tone="warning"] {
        color: #B97810;
    }
    QLabel[tone="error"] {
        color: #D9534F;
    }
    QLabel[tone="neutral"] {
        color: #6F7785;
    }
    QLabel#StatusChip {
        padding: 5px 10px;
        border-radius: 9px;
        font-weight: 600;
    }
    QLabel#StatusChip[tone="primary"] {
        color: #216FD1;
        background: #EAF3FF;
    }
    QLabel#StatusChip[tone="success"] {
        color: #248950;
        background: #EAF8F0;
    }
    QLabel#StatusChip[tone="warning"] {
        color: #A96908;
        background: #FFF5E5;
    }
    QLabel#StatusChip[tone="error"] {
        color: #B83C38;
        background: #FDEDEC;
    }
    QLabel#StatusChip[tone="neutral"] {
        color: #6F7785;
        background: #F1F3F6;
    }
    QPushButton {
        min-height: 36px;
        padding: 0 16px;
        background: #FFFFFF;
        border: 1px solid #D8DDE6;
        border-radius: 8px;
    }
    QPushButton:hover {
        background: #F7F9FC;
        border-color: #BFC7D4;
    }
    QPushButton:disabled {
        color: #A9B0BC;
        background: #F3F5F8;
        border-color: #E4E8EF;
    }
    QPushButton[variant="primary"] {
        color: #FFFFFF;
        background: #2F80ED;
        border-color: #2F80ED;
        font-weight: 600;
    }
    QPushButton[variant="primary"]:hover {
        background: #216FD1;
        border-color: #216FD1;
    }
    QPushButton[nav="true"] {
        border: 0;
        background: transparent;
        color: #6F7785;
        padding: 0 12px;
    }
    QPushButton[nav="true"]:checked {
        color: #2F80ED;
        background: #EAF3FF;
    }
    QPushButton[nav="true"]:disabled {
        color: #B5BBC5;
        background: transparent;
    }
    QLineEdit, QComboBox, QSpinBox {
        min-height: 36px;
        padding: 0 10px;
        background: #FFFFFF;
        border: 1px solid #D8DDE6;
        border-radius: 7px;
        selection-background-color: #2F80ED;
    }
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
        border-color: #2F80ED;
    }
    QComboBox::drop-down, QSpinBox::up-button, QSpinBox::down-button {
        border: 0;
        width: 24px;
    }
    QProgressBar {
        min-height: 7px;
        max-height: 7px;
        border: 0;
        border-radius: 3px;
        background: #E4E8EF;
        text-align: center;
    }
    QProgressBar::chunk {
        border-radius: 3px;
        background: #2F80ED;
    }
    QCheckBox#CategoryCheckBox {
        font-size: 16px;
        font-weight: 700;
        spacing: 10px;
    }
    QCheckBox#CategoryCheckBox::indicator {
        width: 18px;
        height: 18px;
    }
    QScrollBar:vertical {
        width: 10px;
        background: transparent;
        margin: 2px;
    }
    QScrollBar::handle:vertical {
        min-height: 28px;
        background: #C8CED8;
        border-radius: 4px;
    }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
        height: 0;
    }
    """
