from __future__ import annotations

from PySide6.QtWidgets import QLabel, QWidget

from mc_han.qt.view_models import CapabilityCardViewModel
from mc_han.qt.widgets.card import Card
from mc_han.qt.widgets.status_chip import StatusChip


class CapabilityCard(Card):
    def __init__(
        self,
        view_model: CapabilityCardViewModel,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.setMinimumHeight(132)

        title = QLabel(view_model.title)
        title.setObjectName("CardTitle")
        title.setWordWrap(True)
        status = StatusChip(view_model.status_text, view_model.tone)
        status.setMaximumWidth(110)
        detail = QLabel(view_model.detail_text)
        detail.setObjectName("MutedLabel")
        detail.setWordWrap(True)

        self.content_layout.addWidget(title)
        self.content_layout.addWidget(status, alignment=status.alignment())
        self.content_layout.addWidget(detail)
        self.content_layout.addStretch()
