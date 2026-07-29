from __future__ import annotations

from decimal import Decimal

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from mc_han.qt.translation_plan_view_models import (
    TranslationPlanPageViewModel,
)
from mc_han.qt.widgets.card import Card
from mc_han.workflow.translation_plan import TranslationPlanMode


class TranslationPlanPage(QScrollArea):
    back_requested = Signal()
    advanced_requested = Signal()
    continue_requested = Signal()
    mode_changed = Signal(object)
    budget_changed = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TranslationPlanPage")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._rendering = False

        content = QWidget()
        content.setObjectName("AppRoot")
        self.page_layout = QVBoxLayout(content)
        self.page_layout.setContentsMargins(32, 28, 32, 32)
        self.page_layout.setSpacing(18)

        title = QLabel("选择汉化方案")
        title.setObjectName("PageTitle")
        subtitle = QLabel(
            "先比较复用量、预计 Token、费用和时间。查看方案不会调用 API。"
        )
        subtitle.setObjectName("MutedLabel")
        subtitle.setWordWrap(True)
        self.page_layout.addWidget(title)
        self.page_layout.addWidget(subtitle)

        mode_card = Card()
        mode_title = QLabel("翻译质量与费用")
        mode_title.setObjectName("CardTitle")
        mode_card.content_layout.addWidget(mode_title)
        mode_row = QHBoxLayout()
        self.mode_group = QButtonGroup(self)
        self.mode_buttons: dict[TranslationPlanMode, QRadioButton] = {}
        labels = {
            TranslationPlanMode.ECONOMY: "经济模式",
            TranslationPlanMode.BALANCED: "平衡模式（推荐）",
            TranslationPlanMode.HIGH_QUALITY: "高质量模式",
        }
        for mode in TranslationPlanMode:
            button = QRadioButton(labels[mode])
            button.setObjectName(f"PlanMode_{mode.value}")
            self.mode_group.addButton(button)
            self.mode_buttons[mode] = button
            mode_row.addWidget(button)
            button.toggled.connect(
                lambda checked, selected=mode: (
                    self.mode_changed.emit(selected)
                    if checked and not self._rendering
                    else None
                )
            )
        mode_row.addStretch()
        mode_card.content_layout.addLayout(mode_row)
        self.page_layout.addWidget(mode_card)

        self.summary_card = Card()
        self.plan_title = QLabel("")
        self.plan_title.setObjectName("SectionTitle")
        self.plan_description = QLabel("")
        self.plan_description.setObjectName("MutedLabel")
        self.plan_description.setWordWrap(True)
        self.summary_card.content_layout.addWidget(self.plan_title)
        self.summary_card.content_layout.addWidget(self.plan_description)
        summary_grid = QGridLayout()
        summary_grid.setHorizontalSpacing(24)
        summary_grid.setVerticalSpacing(12)
        self.summary_values: dict[str, QLabel] = {}
        fields = (
            ("selected", "选择内容"),
            ("reused", "直接复用"),
            ("ai", "AI 新翻译"),
            ("skipped", "跳过"),
            ("requests", "预计请求"),
            ("tokens", "预计 Token"),
            ("cost", "预计费用"),
            ("duration", "预计时间"),
        )
        for index, (key, label_text) in enumerate(fields):
            label = QLabel(label_text)
            label.setObjectName("MutedLabel")
            value = QLabel("")
            value.setObjectName("ValueLabel")
            value.setWordWrap(True)
            row, column = divmod(index, 2)
            summary_grid.addWidget(label, row * 2, column)
            summary_grid.addWidget(value, row * 2 + 1, column)
            self.summary_values[key] = value
        self.summary_card.content_layout.addLayout(summary_grid)
        self.pricing_note = QLabel("")
        self.pricing_note.setObjectName("MutedLabel")
        self.pricing_note.setWordWrap(True)
        self.summary_card.content_layout.addWidget(self.pricing_note)
        self.page_layout.addWidget(self.summary_card)

        route_card = Card()
        route_title = QLabel("模型路由")
        route_title.setObjectName("CardTitle")
        self.route_layout = QVBoxLayout()
        route_card.content_layout.addWidget(route_title)
        route_card.content_layout.addLayout(self.route_layout)
        self.page_layout.addWidget(route_card)

        budget_card = Card()
        budget_title = QLabel("预算保护")
        budget_title.setObjectName("CardTitle")
        budget_row = QHBoxLayout()
        budget_label = QLabel("预算上限")
        self.budget_spin = QDoubleSpinBox()
        self.budget_spin.setObjectName("BudgetLimitSpin")
        self.budget_spin.setRange(0, 100000)
        self.budget_spin.setDecimals(2)
        self.budget_spin.setSingleStep(1)
        self.budget_spin.setPrefix("$ ")
        self.budget_spin.setSpecialValueText("不限制")
        self.budget_spin.valueChanged.connect(self._budget_value_changed)
        budget_row.addWidget(budget_label)
        budget_row.addWidget(self.budget_spin)
        budget_row.addStretch()
        self.budget_message = QLabel("")
        self.budget_message.setWordWrap(True)
        budget_card.content_layout.addWidget(budget_title)
        budget_card.content_layout.addLayout(budget_row)
        budget_card.content_layout.addWidget(self.budget_message)
        self.page_layout.addWidget(budget_card)

        actions = QHBoxLayout()
        self.back_button = QPushButton("返回扫描结果")
        self.advanced_button = QPushButton("高级设置")
        self.continue_button = QPushButton("确认方案并试译")
        self.continue_button.setProperty("variant", "primary")
        self.back_button.clicked.connect(self.back_requested)
        self.advanced_button.clicked.connect(self.advanced_requested)
        self.continue_button.clicked.connect(self.continue_requested)
        actions.addWidget(self.back_button)
        actions.addStretch()
        actions.addWidget(self.advanced_button)
        actions.addWidget(self.continue_button)
        self.page_layout.addLayout(actions)
        self.page_layout.addStretch()
        self.setWidget(content)

    def set_budget(self, budget: Decimal | None) -> None:
        self._rendering = True
        try:
            self.budget_spin.setValue(float(budget or Decimal("0")))
        finally:
            self._rendering = False

    def show_plan(self, view_model: TranslationPlanPageViewModel) -> None:
        self._rendering = True
        try:
            self.mode_buttons[view_model.mode].setChecked(True)
        finally:
            self._rendering = False
        self.plan_title.setText(view_model.title)
        self.plan_description.setText(view_model.description)
        self.summary_values["selected"].setText(view_model.selected)
        self.summary_values["reused"].setText(view_model.reused)
        self.summary_values["ai"].setText(view_model.ai_translation)
        self.summary_values["skipped"].setText(view_model.skipped)
        self.summary_values["requests"].setText(view_model.requests)
        self.summary_values["tokens"].setText(view_model.tokens)
        self.summary_values["cost"].setText(view_model.cost)
        self.summary_values["duration"].setText(view_model.duration)
        self.pricing_note.setText(view_model.pricing_note)
        self.budget_message.setText(view_model.budget_message)
        self.budget_message.setProperty("tone", view_model.budget_tone.value)
        self.budget_message.style().unpolish(self.budget_message)
        self.budget_message.style().polish(self.budget_message)
        _clear_layout(self.route_layout)
        if not view_model.routes:
            empty = QLabel("没有需要发送给 API 的新译文。")
            empty.setObjectName("MutedLabel")
            self.route_layout.addWidget(empty)
        for route in view_model.routes:
            row = QHBoxLayout()
            category = QLabel(route.category)
            model = QLabel(route.model)
            model.setObjectName("ValueLabel")
            count = QLabel(route.record_count)
            count.setObjectName("MutedLabel")
            row.addWidget(category)
            row.addStretch()
            row.addWidget(model)
            row.addWidget(count)
            self.route_layout.addLayout(row)
        self.continue_button.setEnabled(view_model.can_continue)
        self.continue_button.setToolTip(view_model.disabled_reason)

    def show_failure(self, message: str) -> None:
        self.plan_title.setText("暂时无法生成翻译方案")
        self.plan_description.setText(message)
        self.continue_button.setEnabled(False)
        self.continue_button.setToolTip("请返回扫描或检查高级设置")

    def _budget_value_changed(self, value: float) -> None:
        if self._rendering:
            return
        self.budget_changed.emit(
            None if value <= 0 else Decimal(f"{value:.2f}")
        )


def _clear_layout(layout: QVBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        child_layout = item.layout()
        widget = item.widget()
        if child_layout is not None:
            _clear_layout(child_layout)
        if widget is not None:
            widget.deleteLater()
