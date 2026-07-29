from __future__ import annotations

from PySide6.QtCore import Signal
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

from mc_han.qt.full_translation_view_models import (
    FullTranslationPageViewModel,
)
from mc_han.qt.widgets.card import Card


class FullTranslationPage(QScrollArea):
    back_requested = Signal()
    start_requested = Signal()
    pause_requested = Signal()
    resume_requested = Signal()
    retry_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("FullTranslationPage")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)

        content = QWidget()
        content.setObjectName("AppRoot")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(32, 28, 32, 32)
        layout.setSpacing(18)

        title = QLabel("完整翻译")
        title.setObjectName("PageTitle")
        subtitle = QLabel(
            "仅翻译扫描页选中的内容；已有译文、试译结果和缓存会直接复用。"
        )
        subtitle.setObjectName("MutedLabel")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        notice = Card()
        notice_title = QLabel("开始后将产生 API 费用")
        notice_title.setObjectName("CardTitle")
        notice_text = QLabel(
            "程序不会自动开始。每个批次完成后，现有翻译引擎会立即保存 CSV 和缓存。"
        )
        notice_text.setObjectName("MutedLabel")
        notice_text.setWordWrap(True)
        notice.content_layout.addWidget(notice_title)
        notice.content_layout.addWidget(notice_text)
        layout.addWidget(notice)

        progress_card = Card()
        self.status_title = QLabel("等待用户确认")
        self.status_title.setObjectName("CardTitle")
        self.status_detail = QLabel("点击开始完整翻译后才会调用 Provider。")
        self.status_detail.setObjectName("MutedLabel")
        self.status_detail.setWordWrap(True)
        self.progress_bar = QProgressBar()
        progress_card.content_layout.addWidget(self.status_title)
        progress_card.content_layout.addWidget(self.status_detail)
        progress_card.content_layout.addWidget(self.progress_bar)
        layout.addWidget(progress_card)

        summary = Card()
        summary_title = QLabel("翻译进度")
        summary_title.setObjectName("CardTitle")
        summary.content_layout.addWidget(summary_title)
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(12)
        self.summary_values: dict[str, QLabel] = {}
        fields = (
            ("completed", "完成 / 总数"),
            ("category", "当前分类"),
            ("success", "成功"),
            ("failed", "失败"),
            ("tokens", "总 Token"),
            ("cost", "费用"),
            ("elapsed", "耗时"),
            ("history_reuse", "历史 / 试译复用"),
            ("cache_reuse", "缓存复用"),
            ("api_new", "API 新翻译"),
            ("remaining", "剩余"),
            ("eta", "预计剩余"),
            ("budget", "预算"),
        )
        for index, (key, label) in enumerate(fields):
            row = (index // 4) * 2
            column = index % 4
            name = QLabel(label)
            name.setObjectName("MutedLabel")
            value = QLabel("-")
            value.setObjectName("ValueLabel")
            value.setWordWrap(True)
            grid.addWidget(name, row, column)
            grid.addWidget(value, row + 1, column)
            self.summary_values[key] = value
        summary.content_layout.addLayout(grid)
        layout.addWidget(summary)

        activity = Card()
        activity_title = QLabel("最近活动")
        activity_title.setObjectName("CardTitle")
        self.activity_layout = QVBoxLayout()
        self.activity_empty = QLabel("开始后显示最近完成的译文和失败项。")
        self.activity_empty.setObjectName("MutedLabel")
        self.activity_layout.addWidget(self.activity_empty)
        activity.content_layout.addWidget(activity_title)
        activity.content_layout.addLayout(self.activity_layout)
        layout.addWidget(activity)

        self.failure_label = QLabel("")
        self.failure_label.setProperty("tone", "error")
        self.failure_label.setWordWrap(True)
        self.failure_label.hide()
        layout.addWidget(self.failure_label)

        actions = QHBoxLayout()
        self.back_button = QPushButton("返回试译结果")
        self.start_button = QPushButton("开始完整翻译")
        self.start_button.setProperty("variant", "primary")
        self.pause_button = QPushButton("暂停")
        self.resume_button = QPushButton("继续")
        self.retry_button = QPushButton("只重试失败记录")
        self.back_button.clicked.connect(self.back_requested)
        self.start_button.clicked.connect(self.start_requested)
        self.pause_button.clicked.connect(self.pause_requested)
        self.resume_button.clicked.connect(self.resume_requested)
        self.retry_button.clicked.connect(self.retry_requested)
        actions.addWidget(self.back_button)
        actions.addStretch()
        actions.addWidget(self.pause_button)
        actions.addWidget(self.resume_button)
        actions.addWidget(self.retry_button)
        actions.addWidget(self.start_button)
        layout.addLayout(actions)
        layout.addStretch()
        self.setWidget(content)

    def show_ready(self, view_model: FullTranslationPageViewModel) -> None:
        self.status_title.setText("等待用户确认")
        self.status_detail.setText(
            "点击开始后才会调用翻译服务；已完成内容不会重复请求。"
        )
        self.failure_label.hide()
        self._render(view_model)
        self.back_button.setEnabled(True)
        self.start_button.setEnabled(view_model.remaining_count > 0)
        self.start_button.show()
        self.pause_button.setEnabled(False)
        self.resume_button.setEnabled(False)
        self.retry_button.setEnabled(False)

    def show_running(self, *, retry: bool = False) -> None:
        self.status_title.setText(
            "正在重试失败记录" if retry else "正在完整翻译"
        )
        self.status_detail.setText(
            "当前请求会正常完成；暂停只会阻止下一批开始。"
        )
        self.failure_label.hide()
        self.back_button.setEnabled(False)
        self.start_button.setEnabled(False)
        self.pause_button.setEnabled(True)
        self.resume_button.setEnabled(False)
        self.retry_button.setEnabled(False)

    def show_pause_requested(self) -> None:
        self.status_title.setText("正在等待安全暂停")
        self.status_detail.setText(
            "当前 API 请求不会被中断，完成后将在下一批开始前暂停。"
        )
        self.pause_button.setEnabled(False)
        self.resume_button.setEnabled(True)

    def show_resumed(self) -> None:
        self.status_title.setText("正在完整翻译")
        self.status_detail.setText("已恢复，正在继续处理后续批次。")
        self.pause_button.setEnabled(True)
        self.resume_button.setEnabled(False)

    def update_progress(
        self,
        view_model: FullTranslationPageViewModel,
    ) -> None:
        self._render(view_model)

    def show_current_category(self, category: str) -> None:
        self.summary_values["category"].setText(category)

    def show_result(
        self,
        view_model: FullTranslationPageViewModel,
    ) -> None:
        self._render(view_model)
        self.back_button.setEnabled(True)
        self.start_button.hide()
        self.pause_button.setEnabled(False)
        self.resume_button.setEnabled(False)
        self.retry_button.setEnabled(view_model.can_retry)
        if view_model.can_retry:
            self.status_title.setText("翻译完成，但有失败记录")
            self.status_detail.setText(
                "成功批次已经保存，可仅重试失败记录。"
            )
        elif view_model.remaining_count:
            self.status_title.setText("翻译已安全停止")
            self.status_detail.setText(
                "已完成批次已经保存，下次进入可继续。"
            )
        else:
            self.status_title.setText("完整翻译已完成")
            self.status_detail.setText("所有已选内容均已有译文。")

    def show_failure(self, message: str) -> None:
        self.status_title.setText("完整翻译未能继续")
        self.status_detail.setText("已经完成的批次、CSV 和缓存不会丢失。")
        self.failure_label.setText(message)
        self.failure_label.show()
        self.back_button.setEnabled(True)
        self.start_button.show()
        self.start_button.setEnabled(True)
        self.pause_button.setEnabled(False)
        self.resume_button.setEnabled(False)
        self.retry_button.setEnabled(False)

    def _render(self, view_model: FullTranslationPageViewModel) -> None:
        self.summary_values["completed"].setText(
            view_model.completed_text
        )
        self.summary_values["category"].setText(
            view_model.current_category
        )
        self.summary_values["success"].setText(
            view_model.successful_text
        )
        self.summary_values["failed"].setText(view_model.failed_text)
        self.summary_values["tokens"].setText(view_model.tokens_text)
        self.summary_values["cost"].setText(view_model.cost_text)
        self.summary_values["elapsed"].setText(view_model.elapsed_text)
        self.summary_values["history_reuse"].setText(
            view_model.historical_reuse_text
        )
        self.summary_values["cache_reuse"].setText(
            view_model.cache_reuse_text
        )
        self.summary_values["api_new"].setText(view_model.api_new_text)
        self.summary_values["remaining"].setText(view_model.remaining_text)
        self.summary_values["eta"].setText(view_model.eta_text)
        self.summary_values["budget"].setText(view_model.budget_text)
        self.progress_bar.setRange(0, max(1, view_model.total_count))
        self.progress_bar.setValue(view_model.completed_count)

    def show_activity(self, messages: tuple[str, ...]) -> None:
        while self.activity_layout.count():
            item = self.activity_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for message in messages[-12:]:
            label = QLabel(message)
            label.setObjectName("MutedLabel")
            label.setWordWrap(True)
            self.activity_layout.addWidget(label)
