from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QWidget

from mc_han.qt.view_models import StatusTone


class StatusChip(QLabel):
    def __init__(
        self,
        text: str = "",
        tone: StatusTone = StatusTone.NEUTRAL,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self.setObjectName("StatusChip")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(
            self.sizePolicy().horizontalPolicy(),
            self.sizePolicy().verticalPolicy(),
        )
        self.set_tone(tone)

    def set_tone(self, tone: StatusTone) -> None:
        self.setProperty("tone", tone.value)
        self.style().unpolish(self)
        self.style().polish(self)
