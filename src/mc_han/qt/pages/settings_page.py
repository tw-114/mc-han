from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QComboBox,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from mc_han.qt.widgets.card import Card


class SettingsPage(QScrollArea):
    back_requested = Signal()
    delete_credentials_requested = Signal()
    theme_changed = Signal(object)

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
            "翻译服务和性能参数会保存在本机；API Key 仅通过 Windows "
            "安全凭据存储保存。"
        )
        subtitle.setObjectName("MutedLabel")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        appearance = Card()
        appearance_title = QLabel("外观")
        appearance_title.setObjectName("SectionTitle")
        appearance_text = QLabel(
            "跟随系统会读取 Windows 的应用主题；手动选择后会在重启后保留。"
        )
        appearance_text.setObjectName("MutedLabel")
        appearance_text.setWordWrap(True)
        self.theme_combo = QComboBox()
        self.theme_combo.setObjectName("ThemeModeCombo")
        self.theme_combo.addItem("跟随系统", "system")
        self.theme_combo.addItem("浅色", "light")
        self.theme_combo.addItem("深色", "dark")
        self.theme_status = QLabel("")
        self.theme_status.setObjectName("MutedLabel")
        self.theme_status.setWordWrap(True)
        self.theme_combo.currentIndexChanged.connect(
            lambda: self.theme_changed.emit(self.theme_combo.currentData())
        )
        appearance.content_layout.addWidget(appearance_title)
        appearance.content_layout.addWidget(appearance_text)
        appearance.content_layout.addWidget(self.theme_combo)
        appearance.content_layout.addWidget(self.theme_status)
        layout.addWidget(appearance)

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
        self.credential_status = QLabel("尚未保存 API Key")
        self.credential_status.setObjectName("MutedLabel")
        self.credential_status.setWordWrap(True)
        self.delete_credentials_button = QPushButton("删除已保存的 API Key")
        self.delete_credentials_button.setEnabled(False)
        self.delete_credentials_button.clicked.connect(
            lambda: self.delete_credentials_requested.emit()
        )
        privacy.content_layout.addWidget(self.credential_status)
        privacy.content_layout.addWidget(self.delete_credentials_button)
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

    def show_credential_status(
        self,
        message: str,
        *,
        can_delete: bool,
    ) -> None:
        self.credential_status.setText(message)
        self.delete_credentials_button.setEnabled(can_delete)
        self.delete_credentials_button.setToolTip(
            "" if can_delete else "当前没有已保存的 API Key"
        )

    def set_theme(self, preference: str, effective: str) -> None:
        index = self.theme_combo.findData(preference)
        self.theme_combo.blockSignals(True)
        try:
            self.theme_combo.setCurrentIndex(max(0, index))
        finally:
            self.theme_combo.blockSignals(False)
        effective_text = "深色" if effective == "dark" else "浅色"
        self.theme_status.setText(f"当前使用：{effective_text}主题")
