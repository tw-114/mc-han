from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from mc_han.qt.translation_config_view_models import (
    PROVIDER_DISPLAY_NAMES,
    TranslationConfigPageViewModel,
    TranslationConfigValidation,
    TranslationProvider,
    TranslationSessionConfig,
)
from mc_han.qt.widgets.card import Card


class TranslationConfigPage(QScrollArea):
    back_requested = Signal()
    restore_requested = Signal()
    validate_requested = Signal()
    continue_requested = Signal()
    provider_changed = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TranslationConfigPage")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._setting_fields = False

        content = QWidget()
        content.setObjectName("AppRoot")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(32, 28, 32, 32)
        layout.setSpacing(18)

        title = QLabel("翻译服务配置")
        title.setObjectName("PageTitle")
        subtitle = QLabel(
            "配置仅保存在当前程序会话。验证配置不会发送翻译请求，也不会产生 API 费用。"
        )
        subtitle.setObjectName("MutedLabel")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        selection_card = Card()
        selection_title = QLabel("本次翻译范围")
        selection_title.setObjectName("CardTitle")
        self.selection_summary = QLabel("")
        self.selection_summary.setObjectName("ValueLabel")
        selection_detail = QLabel("范围来自上一页的扫描分类选择。")
        selection_detail.setObjectName("MutedLabel")
        selection_card.content_layout.addWidget(selection_title)
        selection_card.content_layout.addWidget(self.selection_summary)
        selection_card.content_layout.addWidget(selection_detail)
        layout.addWidget(selection_card)

        config_card = Card()
        config_title = QLabel("服务与请求设置")
        config_title.setObjectName("CardTitle")
        config_card.content_layout.addWidget(config_title)
        form = QFormLayout()
        form.setHorizontalSpacing(24)
        form.setVerticalSpacing(14)
        form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )

        self.provider_combo = QComboBox()
        self.provider_combo.setObjectName("ProviderCombo")
        for provider in TranslationProvider:
            self.provider_combo.addItem(
                PROVIDER_DISPLAY_NAMES[provider],
                provider.value,
            )
        self.provider_combo.currentIndexChanged.connect(
            self._provider_index_changed
        )
        form.addRow("服务商", self.provider_combo)

        self.base_url_edit = QLineEdit()
        self.base_url_edit.setObjectName("BaseUrlEdit")
        self.base_url_edit.setPlaceholderText("https://api.example.com/v1")
        form.addRow("Base URL", self.base_url_edit)

        self.model_edit = QLineEdit()
        self.model_edit.setObjectName("ModelEdit")
        self.model_edit.setPlaceholderText("输入服务商提供的模型名称")
        form.addRow("模型", self.model_edit)

        key_row = QHBoxLayout()
        key_row.setSpacing(8)
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setObjectName("ApiKeyEdit")
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText("仅保存在当前程序会话")
        self.toggle_key_button = QPushButton("显示")
        self.toggle_key_button.setObjectName("ToggleApiKeyButton")
        self.toggle_key_button.setCheckable(True)
        self.toggle_key_button.setToolTip("显示或隐藏 API Key")
        self.toggle_key_button.toggled.connect(self._toggle_api_key)
        key_row.addWidget(self.api_key_edit, stretch=1)
        key_row.addWidget(self.toggle_key_button)
        form.addRow("API Key", key_row)

        self.concurrency_spin = QSpinBox()
        self.concurrency_spin.setObjectName("ConcurrencySpin")
        self.concurrency_spin.setRange(1, 3)
        self.concurrency_spin.setSuffix(" 个并发")
        form.addRow("并发数", self.concurrency_spin)

        self.batch_size_spin = QSpinBox()
        self.batch_size_spin.setObjectName("BatchSizeSpin")
        self.batch_size_spin.setRange(1, 150)
        self.batch_size_spin.setSuffix(" 条")
        form.addRow("批次大小", self.batch_size_spin)

        self.timeout_spin = QSpinBox()
        self.timeout_spin.setObjectName("TimeoutSpin")
        self.timeout_spin.setRange(1, 600)
        self.timeout_spin.setSuffix(" 秒")
        form.addRow("超时", self.timeout_spin)
        config_card.content_layout.addLayout(form)

        key_note = QLabel(
            "API Key 不会写入项目目录、配置文件或日志；关闭程序后即从本次会话中移除。"
        )
        key_note.setObjectName("MutedLabel")
        key_note.setWordWrap(True)
        config_card.content_layout.addWidget(key_note)
        layout.addWidget(config_card)

        self.validation_label = QLabel("")
        self.validation_label.setObjectName("ValidationMessage")
        self.validation_label.setWordWrap(True)
        self.validation_label.hide()
        layout.addWidget(self.validation_label)

        actions = QHBoxLayout()
        self.back_button = QPushButton("返回扫描结果")
        self.restore_button = QPushButton("恢复推荐配置")
        self.validate_button = QPushButton("验证配置")
        self.continue_button = QPushButton("保存并继续")
        self.continue_button.setProperty("variant", "primary")
        self.back_button.clicked.connect(self.back_requested)
        self.restore_button.clicked.connect(self.restore_requested)
        self.validate_button.clicked.connect(self.validate_requested)
        self.continue_button.clicked.connect(self.continue_requested)
        actions.addWidget(self.back_button)
        actions.addStretch()
        actions.addWidget(self.restore_button)
        actions.addWidget(self.validate_button)
        actions.addWidget(self.continue_button)
        layout.addLayout(actions)
        layout.addStretch()

        self.setWidget(content)

    def show_config(self, view_model: TranslationConfigPageViewModel) -> None:
        self.selection_summary.setText(view_model.selection_summary)
        self.set_config(view_model.config)
        self.clear_validation()

    def set_config(self, config: TranslationSessionConfig) -> None:
        self._setting_fields = True
        try:
            index = self.provider_combo.findData(config.provider.value)
            self.provider_combo.setCurrentIndex(index)
            self.base_url_edit.setText(config.base_url)
            self.model_edit.setText(config.model)
            self.api_key_edit.setText(config.api_key)
            self.concurrency_spin.setValue(config.concurrency)
            self.batch_size_spin.setValue(config.batch_size)
            self.timeout_spin.setValue(config.timeout_seconds)
        finally:
            self._setting_fields = False

    def current_config(self) -> TranslationSessionConfig:
        provider = TranslationProvider(self.provider_combo.currentData())
        return TranslationSessionConfig(
            provider=provider,
            base_url=self.base_url_edit.text(),
            model=self.model_edit.text(),
            api_key=self.api_key_edit.text(),
            concurrency=self.concurrency_spin.value(),
            batch_size=self.batch_size_spin.value(),
            timeout_seconds=self.timeout_spin.value(),
        )

    def show_validation(
        self,
        validation: TranslationConfigValidation,
    ) -> None:
        self.validation_label.setText(validation.message)
        self.validation_label.setProperty(
            "tone",
            "success" if validation.valid else "error",
        )
        self.validation_label.style().unpolish(self.validation_label)
        self.validation_label.style().polish(self.validation_label)
        self.validation_label.show()

    def clear_validation(self) -> None:
        self.validation_label.clear()
        self.validation_label.hide()

    def _provider_index_changed(self) -> None:
        if self._setting_fields:
            return
        provider = TranslationProvider(self.provider_combo.currentData())
        self.provider_changed.emit(provider)

    def _toggle_api_key(self, visible: bool) -> None:
        self.api_key_edit.setEchoMode(
            QLineEdit.EchoMode.Normal
            if visible
            else QLineEdit.EchoMode.Password
        )
        self.toggle_key_button.setText("隐藏" if visible else "显示")
