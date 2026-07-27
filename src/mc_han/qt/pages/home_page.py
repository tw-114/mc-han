from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from mc_han.qt.widgets.card import Card


class HomePage(QScrollArea):
    select_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("HomePage")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)

        content = QWidget()
        content.setObjectName("AppRoot")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(32, 28, 32, 32)
        layout.setSpacing(18)

        title = QLabel("让 Minecraft 整合包汉化变得简单")
        title.setObjectName("PageTitle")
        subtitle = QLabel(
            "自动检测、AI 翻译、质量检查和安全安装。\n"
            "不会修改 mods 文件夹中的任何 JAR。"
        )
        subtitle.setObjectName("MutedLabel")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        select_card = Card()
        select_title = QLabel("选择一个整合包")
        select_title.setObjectName("SectionTitle")
        select_hint = QLabel(
            "请选择实例根目录。通常可以在其中看到 mods、config、"
            "resourcepacks 或实例元数据文件。"
        )
        select_hint.setObjectName("MutedLabel")
        select_hint.setWordWrap(True)
        self.select_button = QPushButton("选择整合包")
        self.select_button.setObjectName("SelectModpackButton")
        self.select_button.setProperty("variant", "primary")
        self.select_button.setMinimumWidth(160)
        self.select_button.clicked.connect(lambda: self.select_requested.emit())
        select_card.content_layout.addWidget(select_title)
        select_card.content_layout.addWidget(select_hint)
        select_card.content_layout.addWidget(
            self.select_button,
            alignment=Qt.AlignmentFlag.AlignLeft,
        )
        layout.addWidget(select_card)

        safety_title = QLabel("检测过程保持只读")
        safety_title.setObjectName("SectionTitle")
        layout.addWidget(safety_title)
        safety_row = QHBoxLayout()
        safety_row.setSpacing(14)
        for heading, detail in (
            ("JAR 始终只读", "mods/*.jar 不会被修改或重新打包。"),
            ("不会调用 API", "检测阶段不会连接任何翻译服务。"),
            ("不会生成扫描文件", "不会创建 CSV 或翻译缓存。"),
            ("只读取必要信息", "仅读取元数据和 JAR entry 名称。"),
        ):
            card = Card()
            card.setMinimumWidth(190)
            card_title = QLabel(heading)
            card_title.setObjectName("CardTitle")
            card_detail = QLabel(detail)
            card_detail.setObjectName("MutedLabel")
            card_detail.setWordWrap(True)
            card.content_layout.addWidget(card_title)
            card.content_layout.addWidget(card_detail)
            card.content_layout.addStretch()
            safety_row.addWidget(card)
        layout.addLayout(safety_row)

        recent_title = QLabel("最近项目")
        recent_title.setObjectName("SectionTitle")
        layout.addWidget(recent_title)
        recent_card = Card()
        recent_empty = QLabel("尚无最近项目")
        recent_empty.setObjectName("CardTitle")
        recent_hint = QLabel("完成一次整合包检测后，本次结果会保留在当前窗口中。")
        recent_hint.setObjectName("MutedLabel")
        recent_hint.setWordWrap(True)
        recent_card.content_layout.addWidget(recent_empty)
        recent_card.content_layout.addWidget(recent_hint)
        layout.addWidget(recent_card)
        layout.addStretch()

        self.setWidget(content)
