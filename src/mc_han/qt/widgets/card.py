from __future__ import annotations

from PySide6.QtWidgets import QFrame, QVBoxLayout, QWidget


class Card(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.content_layout = QVBoxLayout(self)
        self.content_layout.setContentsMargins(20, 18, 20, 18)
        self.content_layout.setSpacing(12)
