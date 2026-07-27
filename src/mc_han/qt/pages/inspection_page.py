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

from mc_han.qt.task_runner import TaskFailure
from mc_han.qt.view_models import (
    InspectionPageViewModel,
    MessageViewModel,
    StatusTone,
)
from mc_han.qt.widgets.capability_card import CapabilityCard
from mc_han.qt.widgets.card import Card
from mc_han.qt.widgets.status_chip import StatusChip


DEFAULT_VISIBLE_MESSAGES = 6


class InspectionPage(QScrollArea):
    reselect_requested = Signal()
    home_requested = Signal()
    scan_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("InspectionPage")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._messages: tuple[MessageViewModel, ...] = ()
        self._show_all_messages = False

        content = QWidget()
        content.setObjectName("AppRoot")
        self.page_layout = QVBoxLayout(content)
        self.page_layout.setContentsMargins(32, 28, 32, 32)
        self.page_layout.setSpacing(18)

        page_title = QLabel("整合包检测")
        page_title.setObjectName("PageTitle")
        self.page_layout.addWidget(page_title)

        self.status_card = Card()
        status_row = QHBoxLayout()
        status_row.setSpacing(14)
        self.status_chip = StatusChip("准备检测")
        self.status_chip.setMaximumWidth(170)
        status_text_layout = QVBoxLayout()
        status_text_layout.setSpacing(4)
        self.status_title = QLabel("等待选择整合包")
        self.status_title.setObjectName("SectionTitle")
        self.status_description = QLabel("")
        self.status_description.setObjectName("MutedLabel")
        self.status_description.setWordWrap(True)
        status_text_layout.addWidget(self.status_title)
        status_text_layout.addWidget(self.status_description)
        status_row.addWidget(self.status_chip, alignment=Qt.AlignmentFlag.AlignTop)
        status_row.addLayout(status_text_layout, stretch=1)
        self.status_card.content_layout.addLayout(status_row)
        self.page_layout.addWidget(self.status_card)

        self.loading_card = Card()
        loading_title = QLabel("正在检测整合包")
        loading_title.setObjectName("CardTitle")
        self.loading_detail = QLabel("正在读取实例元数据和 JAR entry 名称…")
        self.loading_detail.setObjectName("MutedLabel")
        self.loading_detail.setWordWrap(True)
        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("InspectionProgress")
        self.progress_bar.setRange(0, 0)
        self.loading_card.content_layout.addWidget(loading_title)
        self.loading_card.content_layout.addWidget(self.loading_detail)
        self.loading_card.content_layout.addWidget(self.progress_bar)
        self.page_layout.addWidget(self.loading_card)

        self.result_content = QWidget()
        result_layout = QVBoxLayout(self.result_content)
        result_layout.setContentsMargins(0, 0, 0, 0)
        result_layout.setSpacing(18)

        basic_title = QLabel("基本信息")
        basic_title.setObjectName("SectionTitle")
        result_layout.addWidget(basic_title)
        basic_card = Card()
        basic_grid = QGridLayout()
        basic_grid.setHorizontalSpacing(24)
        basic_grid.setVerticalSpacing(12)
        self.basic_values: dict[str, QLabel] = {}
        for row, (key, title) in enumerate(
            (
                ("display_name", "整合包名称"),
                ("minecraft_version", "Minecraft"),
                ("loader", "加载器"),
                ("mod_count", "模组数量"),
                ("duration", "检测耗时"),
            )
        ):
            label = QLabel(title)
            label.setObjectName("MutedLabel")
            value = QLabel("未识别")
            value.setObjectName("ValueLabel")
            value.setWordWrap(True)
            basic_grid.addWidget(label, row, 0)
            basic_grid.addWidget(value, row, 1)
            self.basic_values[key] = value
        basic_grid.setColumnStretch(1, 1)
        basic_card.content_layout.addLayout(basic_grid)
        result_layout.addWidget(basic_card)

        capabilities_title = QLabel("可处理内容")
        capabilities_title.setObjectName("SectionTitle")
        result_layout.addWidget(capabilities_title)
        self.capabilities_widget = QWidget()
        self.capabilities_grid = QGridLayout(self.capabilities_widget)
        self.capabilities_grid.setContentsMargins(0, 0, 0, 0)
        self.capabilities_grid.setHorizontalSpacing(14)
        self.capabilities_grid.setVerticalSpacing(14)
        self.capabilities_grid.setColumnStretch(0, 1)
        self.capabilities_grid.setColumnStretch(1, 1)
        self.capabilities_grid.setColumnStretch(2, 1)
        result_layout.addWidget(self.capabilities_widget)

        chinese_title = QLabel("已有中文资源")
        chinese_title.setObjectName("SectionTitle")
        result_layout.addWidget(chinese_title)
        self.chinese_card = Card()
        self.chinese_chip = StatusChip()
        self.chinese_chip.setMaximumWidth(190)
        self.chinese_detail = QLabel()
        self.chinese_detail.setObjectName("MutedLabel")
        self.chinese_detail.setWordWrap(True)
        self.chinese_card.content_layout.addWidget(
            self.chinese_chip,
            alignment=Qt.AlignmentFlag.AlignLeft,
        )
        self.chinese_card.content_layout.addWidget(self.chinese_detail)
        result_layout.addWidget(self.chinese_card)

        self.messages_title = QLabel("检测提示")
        self.messages_title.setObjectName("SectionTitle")
        result_layout.addWidget(self.messages_title)
        self.messages_widget = QWidget()
        self.messages_layout = QVBoxLayout(self.messages_widget)
        self.messages_layout.setContentsMargins(0, 0, 0, 0)
        self.messages_layout.setSpacing(10)
        result_layout.addWidget(self.messages_widget)
        self.show_more_button = QPushButton("查看更多")
        self.show_more_button.setObjectName("ShowMoreMessagesButton")
        self.show_more_button.clicked.connect(self._toggle_messages)
        result_layout.addWidget(
            self.show_more_button,
            alignment=Qt.AlignmentFlag.AlignLeft,
        )

        self.scan_disabled_reason = QLabel("")
        self.scan_disabled_reason.setObjectName("MutedLabel")
        self.scan_disabled_reason.setWordWrap(True)
        result_layout.addWidget(self.scan_disabled_reason)
        self.page_layout.addWidget(self.result_content)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.reselect_button = QPushButton("重新选择")
        self.reselect_button.setObjectName("ReselectButton")
        self.home_button = QPushButton("返回首页")
        self.home_button.setObjectName("ReturnHomeButton")
        self.start_scan_button = QPushButton("开始扫描")
        self.start_scan_button.setObjectName("StartScanButton")
        self.start_scan_button.setProperty("variant", "primary")
        self.reselect_button.clicked.connect(lambda: self.reselect_requested.emit())
        self.home_button.clicked.connect(lambda: self.home_requested.emit())
        self.start_scan_button.clicked.connect(lambda: self.scan_requested.emit())
        actions.addWidget(self.reselect_button)
        actions.addWidget(self.home_button)
        actions.addStretch()
        actions.addWidget(self.start_scan_button)
        self.page_layout.addLayout(actions)
        self.page_layout.addStretch()

        self.setWidget(content)
        self.show_loading()

    def show_loading(self) -> None:
        self.status_chip.setText("检测中")
        self.status_chip.set_tone(StatusTone.PRIMARY)
        self.status_title.setText("正在检测整合包")
        self.status_description.setText("窗口可以继续响应，检测完成后会自动显示结果。")
        self.loading_card.show()
        self.result_content.hide()
        self.progress_bar.setRange(0, 0)
        self.reselect_button.setEnabled(False)
        self.home_button.setEnabled(False)
        self.start_scan_button.setEnabled(False)

    def show_result(self, view_model: InspectionPageViewModel) -> None:
        self.status_chip.setText(view_model.status_title)
        self.status_chip.set_tone(view_model.status_tone)
        self.status_title.setText(view_model.status_title)
        self.status_description.setText(view_model.status_description)
        self.loading_card.hide()
        self.result_content.show()

        self.basic_values["display_name"].setText(view_model.display_name)
        self.basic_values["minecraft_version"].setText(view_model.minecraft_version)
        self.basic_values["loader"].setText(view_model.loader)
        self.basic_values["mod_count"].setText(view_model.mod_count)
        self.basic_values["duration"].setText(view_model.duration)

        _clear_layout(self.capabilities_grid)
        for index, capability in enumerate(view_model.capabilities):
            self.capabilities_grid.addWidget(
                CapabilityCard(capability),
                index // 3,
                index % 3,
            )

        self.chinese_chip.setText(view_model.existing_chinese.title)
        self.chinese_chip.set_tone(view_model.existing_chinese.tone)
        self.chinese_detail.setText(view_model.existing_chinese.detail_text)
        self._messages = view_model.messages
        self._show_all_messages = False
        self._render_messages()

        self.reselect_button.setEnabled(True)
        self.home_button.setEnabled(True)
        self.start_scan_button.setEnabled(view_model.can_continue)
        self.start_scan_button.setToolTip("")
        self.scan_disabled_reason.setText(
            ""
            if view_model.can_continue
            else "当前目录未通过整合包检测，无法开始扫描。"
        )

    def show_failure(self, failure: TaskFailure) -> None:
        self.status_chip.setText("检测未完成")
        self.status_chip.set_tone(StatusTone.ERROR)
        self.status_title.setText("检测未完成")
        self.status_description.setText(failure.message)
        self.loading_card.hide()
        self.result_content.hide()
        self.reselect_button.setEnabled(True)
        self.home_button.setEnabled(True)
        self.start_scan_button.setEnabled(False)
        self.start_scan_button.setToolTip(f"错误类型：{failure.detail}")

    def _toggle_messages(self) -> None:
        self._show_all_messages = not self._show_all_messages
        self._render_messages()

    def _render_messages(self) -> None:
        _clear_layout(self.messages_layout)
        messages = (
            self._messages
            if self._show_all_messages
            else self._messages[:DEFAULT_VISIBLE_MESSAGES]
        )
        for message in messages:
            card = Card()
            heading = QLabel(message.title)
            heading.setObjectName("CardTitle")
            heading.setProperty("tone", message.tone.value)
            body = QLabel(message.message)
            body.setWordWrap(True)
            card.content_layout.addWidget(heading)
            card.content_layout.addWidget(body)
            if message.location:
                location = QLabel(f"位置：{message.location}")
                location.setObjectName("MutedLabel")
                location.setWordWrap(True)
                card.content_layout.addWidget(location)
            self.messages_layout.addWidget(card)
        self.messages_title.setVisible(bool(self._messages))
        self.messages_widget.setVisible(bool(self._messages))
        self.show_more_button.setVisible(
            len(self._messages) > DEFAULT_VISIBLE_MESSAGES
        )
        self.show_more_button.setText(
            "收起" if self._show_all_messages else "查看更多"
        )


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
