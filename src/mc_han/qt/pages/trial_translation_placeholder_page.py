from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget


class TrialTranslationPlaceholderPage(QWidget):
    back_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AppRoot")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.addStretch()
        title = QLabel("准备试译")
        title.setObjectName("PageTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        detail = QLabel("配置已完成，下一步将进行小批量试译。")
        detail.setObjectName("MutedLabel")
        detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.back_button = QPushButton("返回翻译配置")
        self.back_button.clicked.connect(self.back_requested)
        layout.addWidget(title)
        layout.addWidget(detail)
        layout.addWidget(
            self.back_button,
            alignment=Qt.AlignmentFlag.AlignCenter,
        )
        layout.addStretch()
