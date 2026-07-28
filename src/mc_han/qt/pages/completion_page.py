from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from mc_han.qt.build_install_view_models import CompletionPageViewModel


class CompletionPage(QWidget):
    back_requested = Signal()
    open_requested = Signal()
    rollback_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AppRoot")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.addStretch()
        self.title = QLabel("操作完成")
        self.title.setObjectName("PageTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail = QLabel("")
        self.detail.setObjectName("MutedLabel")
        self.detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail.setWordWrap(True)
        self.location = QLabel("")
        self.location.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.location.setWordWrap(True)
        self.feedback = QLabel("")
        self.feedback.setProperty("tone", "error")
        self.feedback.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.feedback.setWordWrap(True)
        self.feedback.hide()
        self.back_button = QPushButton("返回生成结果")
        self.open_button = QPushButton("打开输出目录")
        self.rollback_button = QPushButton("撤销本次安装")
        self.back_button.clicked.connect(self.back_requested)
        self.open_button.clicked.connect(self.open_requested)
        self.rollback_button.clicked.connect(self.rollback_requested)
        layout.addWidget(self.title)
        layout.addWidget(self.detail)
        layout.addWidget(self.location)
        layout.addWidget(self.feedback)
        layout.addWidget(
            self.open_button,
            alignment=Qt.AlignmentFlag.AlignCenter,
        )
        layout.addWidget(
            self.rollback_button,
            alignment=Qt.AlignmentFlag.AlignCenter,
        )
        layout.addWidget(
            self.back_button,
            alignment=Qt.AlignmentFlag.AlignCenter,
        )
        layout.addStretch()

    def show_result(self, view_model: CompletionPageViewModel) -> None:
        self.title.setText(view_model.title)
        self.detail.setText(view_model.detail)
        self.location.setText(view_model.location)
        self.rollback_button.setVisible(view_model.can_rollback)
        self.rollback_button.setEnabled(view_model.can_rollback)
        self.back_button.setEnabled(True)
        self.open_button.setEnabled(True)
        self.feedback.hide()

    def show_running(self, message: str) -> None:
        self.feedback.setProperty("tone", "primary")
        self.feedback.setText(message)
        self.feedback.show()
        self.back_button.setEnabled(False)
        self.open_button.setEnabled(False)
        self.rollback_button.setEnabled(False)

    def show_failure(self, message: str) -> None:
        self.feedback.setProperty("tone", "error")
        self.feedback.setText(message)
        self.feedback.show()
        self.back_button.setEnabled(True)
        self.open_button.setEnabled(True)
        self.rollback_button.setEnabled(True)

