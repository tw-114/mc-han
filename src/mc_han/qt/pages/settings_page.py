from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from mc_han.qt.widgets.card import Card


class SettingsPage(QScrollArea):
    back_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsPage")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)

        content = QWidget()
        content.setObjectName("AppRoot")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(32, 28, 32, 32)
        layout.setSpacing(18)

        title = QLabel("设置")
        title.setObjectName("PageTitle")
        subtitle = QLabel(
            "翻译服务参数在“配置翻译”步骤中设置，并仅保留在当前程序会话。"
        )
        subtitle.setObjectName("MutedLabel")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        privacy = Card()
        privacy_title = QLabel("隐私与数据")
        privacy_title.setObjectName("SectionTitle")
        privacy_text = QLabel(
            "API Key 不写入项目文件或日志。mods/*.jar 始终作为只读输入，"
            "只有安装和撤销步骤会在确认后修改整合包中的输出位置。"
        )
        privacy_text.setObjectName("MutedLabel")
        privacy_text.setWordWrap(True)
        privacy.content_layout.addWidget(privacy_title)
        privacy.content_layout.addWidget(privacy_text)
        layout.addWidget(privacy)

        compatibility = Card()
        compatibility_title = QLabel("资源包兼容性")
        compatibility_title.setObjectName("SectionTitle")
        compatibility_text = QLabel(
            "无法识别 Minecraft 版本时仍可继续；构建页面会明确提示使用的"
            "兼容 pack_format。"
        )
        compatibility_text.setObjectName("MutedLabel")
        compatibility_text.setWordWrap(True)
        compatibility.content_layout.addWidget(compatibility_title)
        compatibility.content_layout.addWidget(compatibility_text)
        layout.addWidget(compatibility)

        self.back_button = QPushButton("返回")
        self.back_button.clicked.connect(lambda: self.back_requested.emit())
        layout.addWidget(self.back_button)
        layout.addStretch()
        self.setWidget(content)
