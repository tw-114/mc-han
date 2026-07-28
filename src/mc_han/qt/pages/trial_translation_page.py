from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from mc_han.qt.trial_view_models import TrialPageViewModel
from mc_han.qt.view_models import StatusTone
from mc_han.qt.widgets.card import Card
from mc_han.qt.widgets.status_chip import StatusChip
from mc_han.workflow.trial_models import TrialProgressEvent


class TrialTranslationPage(QScrollArea):
    back_requested = Signal()
    start_requested = Signal()
    retry_requested = Signal()
    continue_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TrialTranslationPage")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._running = False

        content = QWidget()
        content.setObjectName("AppRoot")
        self.page_layout = QVBoxLayout(content)
        self.page_layout.setContentsMargins(32, 28, 32, 32)
        self.page_layout.setSpacing(18)

        title = QLabel("小批量试译")
        title.setObjectName("PageTitle")
        subtitle = QLabel(
            "先查看少量代表性译文，再决定是否进入完整翻译。"
        )
        subtitle.setObjectName("MutedLabel")
        self.page_layout.addWidget(title)
        self.page_layout.addWidget(subtitle)

        warning_card = Card()
        warning_row = QHBoxLayout()
        warning_row.setSpacing(12)
        warning_chip = StatusChip("产生费用", StatusTone.WARNING)
        warning_text = QLabel(
            "点击“开始试译”后将调用当前翻译服务，并产生少量 API 费用。"
            "页面不会自动发送请求。"
        )
        warning_text.setWordWrap(True)
        warning_row.addWidget(
            warning_chip,
            alignment=Qt.AlignmentFlag.AlignTop,
        )
        warning_row.addWidget(warning_text, stretch=1)
        warning_card.content_layout.addLayout(warning_row)
        self.page_layout.addWidget(warning_card)

        self.progress_card = Card()
        self.progress_title = QLabel("等待用户确认")
        self.progress_title.setObjectName("CardTitle")
        self.progress_detail = QLabel("请先查看下方样本，再决定是否开始试译。")
        self.progress_detail.setObjectName("MutedLabel")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.progress_card.content_layout.addWidget(self.progress_title)
        self.progress_card.content_layout.addWidget(self.progress_detail)
        self.progress_card.content_layout.addWidget(self.progress_bar)
        self.page_layout.addWidget(self.progress_card)

        self.summary_card = Card()
        summary_title = QLabel("本次试译")
        summary_title.setObjectName("CardTitle")
        self.summary_card.content_layout.addWidget(summary_title)
        summary_grid = QGridLayout()
        self.summary_values: dict[str, QLabel] = {}
        for index, (key, label) in enumerate(
            (
                ("success", "成功"),
                ("failed", "失败"),
                ("tokens", "总 Token"),
                ("elapsed", "耗时"),
                ("cost", "费用"),
            )
        ):
            name = QLabel(label)
            name.setObjectName("MutedLabel")
            value = QLabel("-")
            value.setObjectName("ValueLabel")
            summary_grid.addWidget(name, 0, index)
            summary_grid.addWidget(value, 1, index)
            self.summary_values[key] = value
        self.summary_card.content_layout.addLayout(summary_grid)
        self.page_layout.addWidget(self.summary_card)

        samples_title = QLabel("代表性样本")
        samples_title.setObjectName("SectionTitle")
        self.page_layout.addWidget(samples_title)
        self.samples_container = QWidget()
        self.samples_layout = QVBoxLayout(self.samples_container)
        self.samples_layout.setContentsMargins(0, 0, 0, 0)
        self.samples_layout.setSpacing(12)
        self.page_layout.addWidget(self.samples_container)

        self.failure_label = QLabel("")
        self.failure_label.setProperty("tone", "error")
        self.failure_label.setWordWrap(True)
        self.failure_label.hide()
        self.page_layout.addWidget(self.failure_label)

        actions = QHBoxLayout()
        self.back_button = QPushButton("返回调整配置")
        self.start_button = QPushButton("开始试译")
        self.start_button.setProperty("variant", "primary")
        self.retry_button = QPushButton("重试失败样本")
        self.continue_button = QPushButton("确认并准备完整翻译")
        self.continue_button.setProperty("variant", "primary")
        self.back_button.clicked.connect(self.back_requested)
        self.start_button.clicked.connect(self.start_requested)
        self.retry_button.clicked.connect(self.retry_requested)
        self.continue_button.clicked.connect(self.continue_requested)
        actions.addWidget(self.back_button)
        actions.addStretch()
        actions.addWidget(self.retry_button)
        actions.addWidget(self.start_button)
        actions.addWidget(self.continue_button)
        self.page_layout.addLayout(actions)
        self.page_layout.addStretch()
        self.setWidget(content)

    def show_ready(self, view_model: TrialPageViewModel) -> None:
        self._running = False
        self.progress_title.setText("等待用户确认")
        self.progress_detail.setText(
            "点击开始后才会调用翻译服务，并产生少量 API 费用。"
        )
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.failure_label.hide()
        self._render(view_model)
        self.back_button.setEnabled(True)
        self.start_button.setEnabled(bool(view_model.samples))
        self.start_button.show()
        self.retry_button.setEnabled(False)
        self.continue_button.setEnabled(False)

    def show_running(self, *, retry: bool) -> None:
        self._running = True
        self.progress_title.setText(
            "正在重试失败样本" if retry else "正在进行小批量试译"
        )
        self.progress_detail.setText("正在等待翻译服务返回，请勿关闭程序。")
        self.progress_bar.setRange(0, 0)
        self.failure_label.hide()
        self.back_button.setEnabled(False)
        self.start_button.setEnabled(False)
        self.retry_button.setEnabled(False)
        self.continue_button.setEnabled(False)

    def update_progress(self, event: TrialProgressEvent) -> None:
        self.progress_detail.setText(event.message)
        if event.total:
            self.progress_bar.setRange(0, event.total)
            self.progress_bar.setValue(event.completed)

    def show_result(self, view_model: TrialPageViewModel) -> None:
        self._running = False
        self.progress_title.setText("试译完成")
        self.progress_detail.setText(
            "成功结果已写入扫描清单和翻译缓存，后续可直接复用。"
        )
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(1)
        self.failure_label.hide()
        self._render(view_model)
        self.back_button.setEnabled(True)
        self.start_button.hide()
        self.retry_button.setEnabled(view_model.can_retry)
        self.continue_button.setEnabled(view_model.can_continue)

    def show_failure(self, message: str, *, can_retry: bool) -> None:
        self._running = False
        self.progress_title.setText("试译未能完成")
        self.progress_detail.setText(
            "已经成功保存的译文和缓存不会丢失。"
        )
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.failure_label.setText(message)
        self.failure_label.show()
        self.back_button.setEnabled(True)
        self.start_button.setEnabled(False)
        self.start_button.hide()
        self.retry_button.setEnabled(can_retry)
        self.continue_button.setEnabled(False)

    def show_confirmed(self) -> None:
        self.progress_title.setText("试译结果已确认")
        self.progress_detail.setText("完整翻译功能将在下一批接入。")
        self.back_button.setEnabled(True)
        self.start_button.hide()
        self.retry_button.setEnabled(False)
        self.continue_button.setEnabled(False)

    def _render(self, view_model: TrialPageViewModel) -> None:
        self.summary_values["success"].setText(view_model.successful)
        self.summary_values["failed"].setText(view_model.failed)
        self.summary_values["tokens"].setText(view_model.tokens)
        self.summary_values["elapsed"].setText(view_model.elapsed)
        self.summary_values["cost"].setText(view_model.cost)
        while self.samples_layout.count():
            item = self.samples_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for sample in view_model.samples:
            card = Card()
            header = QHBoxLayout()
            category = QLabel(sample.category)
            category.setObjectName("CardTitle")
            status = StatusChip(sample.status_text, sample.tone)
            header.addWidget(category)
            header.addStretch()
            header.addWidget(status)
            original = QLabel(f"原文：{sample.original}")
            original.setWordWrap(True)
            translation = QLabel(f"译文：{sample.translation}")
            translation.setWordWrap(True)
            source = QLabel(f"来源：{sample.source}" if sample.source else "")
            source.setObjectName("MutedLabel")
            card.content_layout.addLayout(header)
            card.content_layout.addWidget(original)
            card.content_layout.addWidget(translation)
            card.content_layout.addWidget(source)
            self.samples_layout.addWidget(card)
