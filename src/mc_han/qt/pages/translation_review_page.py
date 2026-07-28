from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableView,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from mc_han.qt.translation_review_view_models import (
    ReviewFilterId,
    ReviewRowStatus,
    ReviewRowViewModel,
    TranslationReviewPageViewModel,
)
from mc_han.qt.widgets.card import Card


FILTER_OPTIONS = (
    ("全部", ReviewFilterId.ALL),
    ("未翻译", ReviewFilterId.UNTRANSLATED),
    ("失败", ReviewFilterId.FAILED),
    ("未审核", ReviewFilterId.UNREVIEWED),
    ("已审核", ReviewFilterId.REVIEWED),
    ("跳过", ReviewFilterId.SKIPPED),
)


class ReviewTableModel(QAbstractTableModel):
    HEADERS = ("状态", "分类", "模组 / 来源", "原文", "译文")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.rows: tuple[ReviewRowViewModel, ...] = ()

    def set_rows(self, rows: tuple[ReviewRowViewModel, ...]) -> None:
        self.beginResetModel()
        self.rows = tuple(rows)
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Horizontal
            and 0 <= section < len(self.HEADERS)
        ):
            return self.HEADERS[section]
        return None

    def data(
        self,
        index: QModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if not index.isValid() or not 0 <= index.row() < len(self.rows):
            return None
        row = self.rows[index.row()]
        values = (
            row.status_text,
            row.category,
            row.source,
            _single_line(row.original),
            _single_line(row.translation) or "未翻译",
        )
        if role == Qt.ItemDataRole.DisplayRole:
            return values[index.column()]
        if role == Qt.ItemDataRole.ToolTipRole:
            return (
                row.status_text,
                row.category,
                f"{row.source}\n{row.location}",
                row.original,
                row.translation or "未翻译",
            )[index.column()]
        if role == Qt.ItemDataRole.ForegroundRole and index.column() == 0:
            return QColor(_status_color(row.status))
        if role == Qt.ItemDataRole.UserRole:
            return row.text_id
        return None


class TranslationReviewPage(QScrollArea):
    back_requested = Signal()
    save_requested = Signal(str, str)
    approve_requested = Signal(str)
    needs_retranslate_requested = Signal(str)
    skip_requested = Signal(str)
    retranslate_placeholder_requested = Signal(str)
    continue_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TranslationReviewPage")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._view_model: TranslationReviewPageViewModel | None = None

        content = QWidget()
        content.setObjectName("AppRoot")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(32, 28, 32, 32)
        layout.setSpacing(16)

        title = QLabel("译文检查")
        title.setObjectName("PageTitle")
        subtitle = QLabel(
            "检查质量问题、编辑译文并记录审核状态。保存会原子更新现有清单。"
        )
        subtitle.setObjectName("MutedLabel")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        summary_card = Card()
        summary_grid = QGridLayout()
        summary_grid.setHorizontalSpacing(24)
        self.summary_values: dict[str, QLabel] = {}
        for column, (key, label) in enumerate(
            (
                ("total", "总条目"),
                ("translated", "已翻译"),
                ("reviewed", "已审核"),
                ("skipped", "已跳过"),
                ("issues", "检查问题"),
            )
        ):
            name = QLabel(label)
            name.setObjectName("MutedLabel")
            value = QLabel("0")
            value.setObjectName("ValueLabel")
            summary_grid.addWidget(name, 0, column)
            summary_grid.addWidget(value, 1, column)
            self.summary_values[key] = value
        summary_card.content_layout.addLayout(summary_grid)
        layout.addWidget(summary_card)

        controls = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(
            "搜索原文、译文、模组、文件或分类"
        )
        self.filter_combo = QComboBox()
        for label, filter_id in FILTER_OPTIONS:
            self.filter_combo.addItem(label, filter_id.value)
        self.visible_count_label = QLabel("0 条")
        self.visible_count_label.setObjectName("MutedLabel")
        controls.addWidget(self.search_input, stretch=1)
        controls.addWidget(self.filter_combo)
        controls.addWidget(self.visible_count_label)
        layout.addLayout(controls)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.table_model = ReviewTableModel(self)
        self.table = QTableView()
        self.table.setModel(self.table_model)
        self.table.setSelectionBehavior(
            QTableView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QTableView.SelectionMode.SingleSelection
        )
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(2, 180)
        splitter.addWidget(self.table)

        detail = Card()
        detail.setMinimumWidth(350)
        self.detail_title = QLabel("选择一条记录")
        self.detail_title.setObjectName("CardTitle")
        self.detail_source = QLabel("")
        self.detail_source.setObjectName("MutedLabel")
        self.detail_source.setWordWrap(True)
        original_label = QLabel("原文")
        original_label.setObjectName("MutedLabel")
        self.original_text = QTextEdit()
        self.original_text.setReadOnly(True)
        self.original_text.setMinimumHeight(100)
        translation_label = QLabel("译文")
        translation_label.setObjectName("MutedLabel")
        self.translation_editor = QTextEdit()
        self.translation_editor.setMinimumHeight(120)
        issues_label = QLabel("现有检查结果")
        issues_label.setObjectName("MutedLabel")
        self.issues_text = QLabel("请选择记录")
        self.issues_text.setWordWrap(True)
        detail.content_layout.addWidget(self.detail_title)
        detail.content_layout.addWidget(self.detail_source)
        detail.content_layout.addWidget(original_label)
        detail.content_layout.addWidget(self.original_text)
        detail.content_layout.addWidget(translation_label)
        detail.content_layout.addWidget(self.translation_editor)
        detail.content_layout.addWidget(issues_label)
        detail.content_layout.addWidget(self.issues_text)

        edit_actions = QHBoxLayout()
        self.save_button = QPushButton("保存译文")
        self.approve_button = QPushButton("标记已审核")
        self.needs_retranslate_button = QPushButton("需要重译")
        self.skip_button = QPushButton("跳过")
        edit_actions.addWidget(self.save_button)
        edit_actions.addWidget(self.approve_button)
        edit_actions.addWidget(self.needs_retranslate_button)
        edit_actions.addWidget(self.skip_button)
        detail.content_layout.addLayout(edit_actions)
        self.retranslate_button = QPushButton("重新翻译当前项")
        detail.content_layout.addWidget(self.retranslate_button)
        splitter.addWidget(detail)
        splitter.setSizes((680, 380))
        layout.addWidget(splitter, stretch=1)

        self.feedback_label = QLabel("")
        self.feedback_label.setWordWrap(True)
        self.feedback_label.hide()
        layout.addWidget(self.feedback_label)

        actions = QHBoxLayout()
        self.back_button = QPushButton("返回完整翻译")
        self.continue_button = QPushButton("继续生成资源包")
        self.continue_button.setProperty("variant", "primary")
        actions.addWidget(self.back_button)
        actions.addStretch()
        actions.addWidget(self.continue_button)
        layout.addLayout(actions)
        self.setWidget(content)

        self.search_input.textChanged.connect(self._apply_filter)
        self.filter_combo.currentIndexChanged.connect(self._apply_filter)
        self.table.selectionModel().selectionChanged.connect(
            self._selection_changed
        )
        self.back_button.clicked.connect(self.back_requested)
        self.save_button.clicked.connect(self._request_save)
        self.approve_button.clicked.connect(self._request_approve)
        self.needs_retranslate_button.clicked.connect(
            self._request_needs_retranslate
        )
        self.skip_button.clicked.connect(self._request_skip)
        self.retranslate_button.clicked.connect(
            self._request_retranslate_placeholder
        )
        self.continue_button.clicked.connect(self.continue_requested)
        self._set_detail_enabled(False)

    @property
    def selected_text_id(self) -> str:
        index = self.table.currentIndex()
        if not index.isValid() or not 0 <= index.row() < len(
            self.table_model.rows
        ):
            return ""
        return self.table_model.rows[index.row()].text_id

    def show_review(
        self,
        view_model: TranslationReviewPageViewModel,
        *,
        selected_id: str = "",
    ) -> None:
        self._view_model = view_model
        self.summary_values["total"].setText(f"{view_model.total_count:,}")
        self.summary_values["translated"].setText(
            f"{view_model.translated_count:,}"
        )
        self.summary_values["reviewed"].setText(
            f"{view_model.reviewed_count:,}"
        )
        self.summary_values["skipped"].setText(
            f"{view_model.skipped_count:,}"
        )
        self.summary_values["issues"].setText(
            f"{view_model.error_count:,} 错误 · "
            f"{view_model.warning_count:,} 警告"
        )
        self.continue_button.setEnabled(True)
        self.feedback_label.hide()
        self._apply_filter()
        self._select_record(selected_id)

    def show_load_failure(self, message: str) -> None:
        self._view_model = None
        self.table_model.set_rows(())
        self.visible_count_label.setText("0 条")
        self.feedback_label.setProperty("tone", "error")
        self.feedback_label.setText(message)
        self.feedback_label.show()
        self._set_detail_enabled(False)
        self.continue_button.setEnabled(False)

    def show_feedback(self, message: str, *, error: bool = False) -> None:
        self.feedback_label.setProperty(
            "tone",
            "error" if error else "success",
        )
        self.feedback_label.setText(message)
        self.feedback_label.show()
        self.feedback_label.style().unpolish(self.feedback_label)
        self.feedback_label.style().polish(self.feedback_label)

    def _apply_filter(self, *_args) -> None:
        if self._view_model is None:
            self.table_model.set_rows(())
            return
        selected_id = self.selected_text_id
        filter_id = ReviewFilterId(
            self.filter_combo.currentData() or ReviewFilterId.ALL.value
        )
        rows = self._view_model.filtered_rows(
            self.search_input.text(),
            filter_id,
        )
        self.table_model.set_rows(rows)
        self.visible_count_label.setText(f"{len(rows):,} 条")
        self._select_record(selected_id)
        if not self.table.currentIndex().isValid() and rows:
            self.table.selectRow(0)
        elif not rows:
            self._clear_detail()

    def _select_record(self, text_id: str) -> None:
        if text_id:
            for index, row in enumerate(self.table_model.rows):
                if row.text_id == text_id:
                    self.table.selectRow(index)
                    return
        if self.table_model.rows:
            self.table.selectRow(0)

    def _selection_changed(self, *_args) -> None:
        index = self.table.currentIndex()
        if not index.isValid() or not 0 <= index.row() < len(
            self.table_model.rows
        ):
            self._clear_detail()
            return
        row = self.table_model.rows[index.row()]
        self.detail_title.setText(
            f"{row.category} · {row.status_text}"
        )
        self.detail_source.setText(f"{row.source}\n{row.location}")
        self.original_text.setPlainText(row.original)
        self.translation_editor.setPlainText(row.translation)
        self.issues_text.setText(
            "\n".join(row.issues) if row.issues else "未发现问题"
        )
        self._set_detail_enabled(True)
        self.approve_button.setEnabled(bool(row.translation.strip()))

    def _clear_detail(self) -> None:
        self.detail_title.setText("选择一条记录")
        self.detail_source.clear()
        self.original_text.clear()
        self.translation_editor.clear()
        self.issues_text.setText("请选择记录")
        self._set_detail_enabled(False)

    def _set_detail_enabled(self, enabled: bool) -> None:
        self.translation_editor.setEnabled(enabled)
        self.save_button.setEnabled(enabled)
        self.approve_button.setEnabled(enabled)
        self.needs_retranslate_button.setEnabled(enabled)
        self.skip_button.setEnabled(enabled)
        self.retranslate_button.setEnabled(enabled)

    def _request_save(self) -> None:
        if text_id := self.selected_text_id:
            self.save_requested.emit(
                text_id,
                self.translation_editor.toPlainText(),
            )

    def _request_approve(self) -> None:
        if text_id := self.selected_text_id:
            self.approve_requested.emit(text_id)

    def _request_needs_retranslate(self) -> None:
        if text_id := self.selected_text_id:
            self.needs_retranslate_requested.emit(text_id)

    def _request_skip(self) -> None:
        if text_id := self.selected_text_id:
            self.skip_requested.emit(text_id)

    def _request_retranslate_placeholder(self) -> None:
        if text_id := self.selected_text_id:
            self.retranslate_placeholder_requested.emit(text_id)


def _single_line(text: str, limit: int = 120) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3] + "..."


def _status_color(status: ReviewRowStatus) -> str:
    if status is ReviewRowStatus.REVIEWED:
        return "#2EAD65"
    if status in {
        ReviewRowStatus.FAILED,
        ReviewRowStatus.NEEDS_RETRANSLATE,
    }:
        return "#D9534F"
    if status in {
        ReviewRowStatus.UNTRANSLATED,
        ReviewRowStatus.UNREVIEWED,
    }:
        return "#B97810"
    return "#6F7785"
