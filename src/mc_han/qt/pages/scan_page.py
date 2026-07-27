from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
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

from mc_han.qt.scan_view_models import (
    ScanCategoryCardViewModel,
    ScanPageViewModel,
    ScanProgressViewModel,
)
from mc_han.qt.task_runner import TaskFailure
from mc_han.qt.view_models import StatusTone
from mc_han.qt.widgets.card import Card
from mc_han.qt.widgets.status_chip import StatusChip
from mc_han.workflow.scan_models import ScanCategoryId


DEFAULT_VISIBLE_DIAGNOSTICS = 5


class ScanPage(QScrollArea):
    back_requested = Signal()
    rescan_requested = Signal()
    continue_requested = Signal()
    select_all_requested = Signal()
    clear_selection_requested = Signal()
    restore_defaults_requested = Signal()
    category_toggled = Signal(object, bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ScanPage")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._category_checks: dict[ScanCategoryId, QCheckBox] = {}
        self._diagnostics = ()
        self._show_all_diagnostics = False

        content = QWidget()
        content.setObjectName("AppRoot")
        self.page_layout = QVBoxLayout(content)
        self.page_layout.setContentsMargins(32, 28, 32, 32)
        self.page_layout.setSpacing(18)

        title = QLabel("扫描与分类")
        title.setObjectName("PageTitle")
        self.page_layout.addWidget(title)

        self.status_card = Card()
        status_row = QHBoxLayout()
        status_row.setSpacing(14)
        self.status_chip = StatusChip("准备扫描")
        status_text = QVBoxLayout()
        self.status_title = QLabel("等待开始扫描")
        self.status_title.setObjectName("SectionTitle")
        self.status_description = QLabel("")
        self.status_description.setObjectName("MutedLabel")
        self.status_description.setWordWrap(True)
        status_text.addWidget(self.status_title)
        status_text.addWidget(self.status_description)
        status_row.addWidget(self.status_chip, alignment=Qt.AlignmentFlag.AlignTop)
        status_row.addLayout(status_text, stretch=1)
        self.status_card.content_layout.addLayout(status_row)
        self.page_layout.addWidget(self.status_card)

        self.loading_card = Card()
        loading_title = QLabel("正在扫描整合包")
        loading_title.setObjectName("CardTitle")
        self.loading_stage = QLabel("正在准备")
        self.loading_stage.setObjectName("ValueLabel")
        self.loading_source = QLabel("")
        self.loading_source.setObjectName("MutedLabel")
        self.loading_source.setWordWrap(True)
        self.discovered_records = QLabel("已发现 0 条内容")
        self.discovered_records.setObjectName("MutedLabel")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        safety = QLabel("扫描只读取受支持内容，不会修改 mods/*.jar，也不会调用翻译 API。")
        safety.setObjectName("MutedLabel")
        safety.setWordWrap(True)
        self.loading_card.content_layout.addWidget(loading_title)
        self.loading_card.content_layout.addWidget(self.loading_stage)
        self.loading_card.content_layout.addWidget(self.loading_source)
        self.loading_card.content_layout.addWidget(self.discovered_records)
        self.loading_card.content_layout.addWidget(self.progress_bar)
        self.loading_card.content_layout.addWidget(safety)
        self.page_layout.addWidget(self.loading_card)

        self.result_content = QWidget()
        result_layout = QVBoxLayout(self.result_content)
        result_layout.setContentsMargins(0, 0, 0, 0)
        result_layout.setSpacing(18)

        summary_title = QLabel("扫描汇总")
        summary_title.setObjectName("SectionTitle")
        result_layout.addWidget(summary_title)
        summary_card = Card()
        summary_grid = QGridLayout()
        summary_grid.setHorizontalSpacing(28)
        summary_grid.setVerticalSpacing(12)
        self.summary_values: dict[str, QLabel] = {}
        summary_fields = (
            ("project", "整合包"),
            ("records", "可翻译条目"),
            ("files", "文件数量"),
            ("sources", "来源数量"),
            ("duration", "扫描耗时"),
            ("warnings", "警告数量"),
        )
        for index, (key, label_text) in enumerate(summary_fields):
            row = index // 3
            column = (index % 3) * 2
            label = QLabel(label_text)
            label.setObjectName("MutedLabel")
            value = QLabel("0")
            value.setObjectName("ValueLabel")
            value.setWordWrap(True)
            summary_grid.addWidget(label, row, column)
            summary_grid.addWidget(value, row, column + 1)
            self.summary_values[key] = value
        summary_card.content_layout.addLayout(summary_grid)
        result_layout.addWidget(summary_card)

        selection_header = QHBoxLayout()
        category_title = QLabel("选择准备翻译的类别")
        category_title.setObjectName("SectionTitle")
        selection_header.addWidget(category_title)
        selection_header.addStretch()
        self.select_all_button = QPushButton("全选")
        self.clear_button = QPushButton("全部取消")
        self.restore_button = QPushButton("恢复推荐")
        self.select_all_button.clicked.connect(
            lambda: self.select_all_requested.emit()
        )
        self.clear_button.clicked.connect(
            lambda: self.clear_selection_requested.emit()
        )
        self.restore_button.clicked.connect(
            lambda: self.restore_defaults_requested.emit()
        )
        selection_header.addWidget(self.select_all_button)
        selection_header.addWidget(self.clear_button)
        selection_header.addWidget(self.restore_button)
        result_layout.addLayout(selection_header)

        self.selection_summary = QLabel("")
        self.selection_summary.setObjectName("ValueLabel")
        result_layout.addWidget(self.selection_summary)
        self.categories_widget = QWidget()
        self.categories_grid = QGridLayout(self.categories_widget)
        self.categories_grid.setContentsMargins(0, 0, 0, 0)
        self.categories_grid.setHorizontalSpacing(14)
        self.categories_grid.setVerticalSpacing(14)
        self.categories_grid.setColumnStretch(0, 1)
        self.categories_grid.setColumnStretch(1, 1)
        result_layout.addWidget(self.categories_widget)

        info_title = QLabel("扫描信息")
        info_title.setObjectName("SectionTitle")
        result_layout.addWidget(info_title)
        self.information_widget = QWidget()
        self.information_grid = QGridLayout(self.information_widget)
        self.information_grid.setContentsMargins(0, 0, 0, 0)
        self.information_grid.setHorizontalSpacing(14)
        self.information_grid.setVerticalSpacing(14)
        self.information_grid.setColumnStretch(0, 1)
        self.information_grid.setColumnStretch(1, 1)
        result_layout.addWidget(self.information_widget)

        diagnostics_header = QHBoxLayout()
        self.diagnostics_title = QLabel("技术日志")
        self.diagnostics_title.setObjectName("SectionTitle")
        diagnostics_header.addWidget(self.diagnostics_title)
        diagnostics_header.addStretch()
        self.diagnostics_toggle = QPushButton("查看详情")
        self.diagnostics_toggle.clicked.connect(self._toggle_diagnostics)
        diagnostics_header.addWidget(self.diagnostics_toggle)
        result_layout.addLayout(diagnostics_header)
        self.diagnostics_widget = QWidget()
        self.diagnostics_layout = QVBoxLayout(self.diagnostics_widget)
        self.diagnostics_layout.setContentsMargins(0, 0, 0, 0)
        self.diagnostics_layout.setSpacing(10)
        result_layout.addWidget(self.diagnostics_widget)

        self.page_layout.addWidget(self.result_content)

        self.failure_card = Card()
        failure_title = QLabel("扫描未能完成")
        failure_title.setObjectName("CardTitle")
        failure_title.setProperty("tone", "error")
        self.failure_message = QLabel("")
        self.failure_message.setWordWrap(True)
        self.failure_saved = QLabel("")
        self.failure_saved.setObjectName("MutedLabel")
        self.failure_saved.setWordWrap(True)
        suggestion = QLabel("建议确认整合包目录仍可访问，然后点击“重新扫描”。")
        suggestion.setObjectName("MutedLabel")
        suggestion.setWordWrap(True)
        self.failure_card.content_layout.addWidget(failure_title)
        self.failure_card.content_layout.addWidget(self.failure_message)
        self.failure_card.content_layout.addWidget(self.failure_saved)
        self.failure_card.content_layout.addWidget(suggestion)
        self.page_layout.addWidget(self.failure_card)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.back_button = QPushButton("返回检测结果")
        self.rescan_button = QPushButton("重新扫描")
        self.continue_button = QPushButton("继续配置翻译")
        self.continue_button.setProperty("variant", "primary")
        self.back_button.clicked.connect(lambda: self.back_requested.emit())
        self.rescan_button.clicked.connect(lambda: self.rescan_requested.emit())
        self.continue_button.clicked.connect(lambda: self.continue_requested.emit())
        actions.addWidget(self.back_button)
        actions.addWidget(self.rescan_button)
        actions.addStretch()
        actions.addWidget(self.continue_button)
        self.page_layout.addLayout(actions)
        self.page_layout.addStretch()
        self.setWidget(content)
        self.show_loading("未识别")

    def show_loading(self, project_name: str) -> None:
        self.status_chip.setText("扫描中")
        self.status_chip.set_tone(StatusTone.PRIMARY)
        self.status_title.setText("正在扫描整合包")
        self.status_description.setText(
            f"{project_name} · 窗口可以继续响应，扫描完成后会自动整理分类。"
        )
        self.loading_stage.setText("正在准备")
        self.loading_source.setText("")
        self.discovered_records.setText("已发现 0 条内容")
        self.progress_bar.setRange(0, 0)
        self.loading_card.show()
        self.result_content.hide()
        self.failure_card.hide()
        self.back_button.setEnabled(False)
        self.rescan_button.setEnabled(False)
        self.continue_button.setEnabled(False)

    def update_progress(self, view_model: ScanProgressViewModel) -> None:
        self.loading_stage.setText(view_model.stage_text)
        self.loading_source.setText(view_model.source_text)
        self.loading_source.setVisible(bool(view_model.source_text))
        self.discovered_records.setText(view_model.discovered_text)

    def show_result(self, view_model: ScanPageViewModel) -> None:
        self.status_chip.setText("扫描完成")
        self.status_chip.set_tone(StatusTone.SUCCESS)
        self.status_title.setText("扫描与分类已完成")
        self.status_description.setText(
            "请选择准备翻译的类别。此操作不会调用 API，也不会产生费用。"
        )
        self.loading_card.hide()
        self.failure_card.hide()
        self.result_content.show()
        self.summary_values["project"].setText(view_model.project_name)
        self.summary_values["records"].setText(view_model.total_records)
        self.summary_values["files"].setText(view_model.total_files)
        self.summary_values["sources"].setText(view_model.total_sources)
        self.summary_values["duration"].setText(view_model.duration)
        self.summary_values["warnings"].setText(view_model.warnings)
        self.selection_summary.setText(view_model.selected_summary)

        _clear_layout(self.categories_grid)
        self._category_checks.clear()
        for index, category in enumerate(view_model.categories):
            card = self._category_card(category)
            self.categories_grid.addWidget(card, index // 2, index % 2)

        _clear_layout(self.information_grid)
        for index, information in enumerate(view_model.information):
            card = Card()
            heading = QLabel(information.title)
            heading.setObjectName("CardTitle")
            heading.setProperty("tone", information.tone.value)
            description = QLabel(information.description)
            description.setWordWrap(True)
            detail = QLabel(information.detail_text)
            detail.setObjectName("MutedLabel")
            detail.setWordWrap(True)
            card.content_layout.addWidget(heading)
            card.content_layout.addWidget(description)
            card.content_layout.addWidget(detail)
            self.information_grid.addWidget(card, index // 2, index % 2)

        self._diagnostics = view_model.diagnostics
        self._show_all_diagnostics = False
        self._render_diagnostics()
        self.back_button.setEnabled(True)
        self.rescan_button.setEnabled(True)
        self.continue_button.setEnabled(view_model.can_continue)
        self.continue_button.setToolTip(
            "" if view_model.can_continue else "请至少选择一个有内容的类别"
        )

    def show_failure(self, failure: TaskFailure) -> None:
        self.status_chip.setText("扫描失败")
        self.status_chip.set_tone(StatusTone.ERROR)
        self.status_title.setText("扫描未能完成")
        self.status_description.setText("可以返回检测结果，或确认目录后重新扫描。")
        self.loading_card.hide()
        self.result_content.hide()
        self.failure_card.show()
        self.failure_message.setText(failure.message)
        self.failure_saved.setText(
            "已生成的扫描清单仍保留在项目目录中。"
            if failure.partial_saved
            else "本次扫描没有保存新的完整清单。"
        )
        self.back_button.setEnabled(True)
        self.rescan_button.setEnabled(failure.retryable)
        self.continue_button.setEnabled(False)

    def _category_card(self, category: ScanCategoryCardViewModel) -> Card:
        card = Card()
        checkbox = QCheckBox(category.title)
        checkbox.setObjectName("CategoryCheckBox")
        checkbox.setChecked(category.selected)
        checkbox.setEnabled(category.enabled)
        checkbox.setToolTip(category.disabled_reason)
        checkbox.toggled.connect(
            lambda checked, category_id=category.category_id: self.category_toggled.emit(
                category_id,
                checked,
            )
        )
        description = QLabel(category.description)
        description.setWordWrap(True)
        count = QLabel(category.count_text)
        count.setObjectName("MutedLabel")
        count.setWordWrap(True)
        card.content_layout.addWidget(checkbox)
        card.content_layout.addWidget(description)
        card.content_layout.addWidget(count)
        self._category_checks[category.category_id] = checkbox
        return card

    def _toggle_diagnostics(self) -> None:
        self._show_all_diagnostics = not self._show_all_diagnostics
        self._render_diagnostics()

    def _render_diagnostics(self) -> None:
        _clear_layout(self.diagnostics_layout)
        diagnostics = (
            self._diagnostics
            if self._show_all_diagnostics
            else self._diagnostics[:DEFAULT_VISIBLE_DIAGNOSTICS]
        )
        for diagnostic in diagnostics:
            card = Card()
            heading = QLabel(diagnostic.title)
            heading.setObjectName("CardTitle")
            heading.setProperty("tone", diagnostic.tone.value)
            message = QLabel(diagnostic.message)
            message.setWordWrap(True)
            card.content_layout.addWidget(heading)
            card.content_layout.addWidget(message)
            if diagnostic.location:
                location = QLabel(f"位置：{diagnostic.location}")
                location.setObjectName("MutedLabel")
                location.setWordWrap(True)
                card.content_layout.addWidget(location)
            self.diagnostics_layout.addWidget(card)
        visible = bool(self._diagnostics)
        self.diagnostics_title.setVisible(visible)
        self.diagnostics_widget.setVisible(visible and self._show_all_diagnostics)
        self.diagnostics_toggle.setVisible(visible)
        self.diagnostics_toggle.setText(
            "收起详情" if self._show_all_diagnostics else "查看详情"
        )


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
