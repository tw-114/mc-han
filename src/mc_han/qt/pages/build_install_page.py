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

from mc_han.qt.build_install_view_models import BuildInstallPageViewModel
from mc_han.qt.widgets.card import Card


class BuildInstallPage(QScrollArea):
    back_requested = Signal()
    build_requested = Signal()
    export_requested = Signal()
    open_requested = Signal()
    install_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("BuildInstallPage")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)

        content = QWidget()
        content.setObjectName("AppRoot")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(32, 28, 32, 32)
        layout.setSpacing(18)

        title = QLabel("生成与安装")
        title.setObjectName("PageTitle")
        subtitle = QLabel(
            "根据已保存译文生成客户端资源包和服务器配置覆盖。"
            "原始 mods/*.jar 始终保持只读。"
        )
        subtitle.setObjectName("MutedLabel")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        self.status_card = Card()
        self.status_title = QLabel("等待生成")
        self.status_title.setObjectName("CardTitle")
        self.status_detail = QLabel(
            "点击生成后才会读取译文清单并写入输出目录。"
        )
        self.status_detail.setObjectName("MutedLabel")
        self.status_detail.setWordWrap(True)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.status_card.content_layout.addWidget(self.status_title)
        self.status_card.content_layout.addWidget(self.status_detail)
        self.status_card.content_layout.addWidget(self.progress_bar)
        layout.addWidget(self.status_card)

        summary = Card()
        heading = QLabel("构建结果")
        heading.setObjectName("CardTitle")
        summary.content_layout.addWidget(heading)
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(12)
        self.summary_values: dict[str, QLabel] = {}
        for index, (key, label) in enumerate(
            (
                ("name", "导出文件名"),
                ("entries", "可安装条目"),
                ("resource", "资源文件"),
                ("config", "配置文件"),
                ("install", "安装变化"),
                ("pack_format", "pack_format"),
            )
        ):
            row = (index // 3) * 2
            column = index % 3
            name = QLabel(label)
            name.setObjectName("MutedLabel")
            value = QLabel("-")
            value.setObjectName("ValueLabel")
            value.setWordWrap(True)
            grid.addWidget(name, row, column)
            grid.addWidget(value, row + 1, column)
            self.summary_values[key] = value
        summary.content_layout.addLayout(grid)
        output_label = QLabel("输出目录")
        output_label.setObjectName("MutedLabel")
        self.output_directory_label = QLabel("-")
        self.output_directory_label.setWordWrap(True)
        summary.content_layout.addWidget(output_label)
        summary.content_layout.addWidget(self.output_directory_label)
        layout.addWidget(summary)

        diagnostics = Card()
        diagnostic_title = QLabel("警告与检查")
        diagnostic_title.setObjectName("CardTitle")
        self.diagnostics_label = QLabel("生成后显示检查结果")
        self.diagnostics_label.setObjectName("MutedLabel")
        self.diagnostics_label.setWordWrap(True)
        diagnostics.content_layout.addWidget(diagnostic_title)
        diagnostics.content_layout.addWidget(self.diagnostics_label)
        layout.addWidget(diagnostics)

        self.feedback_label = QLabel("")
        self.feedback_label.setWordWrap(True)
        self.feedback_label.hide()
        layout.addWidget(self.feedback_label)

        actions = QHBoxLayout()
        self.back_button = QPushButton("返回译文检查")
        self.build_button = QPushButton("生成资源包")
        self.build_button.setProperty("variant", "primary")
        self.export_button = QPushButton("导出 ZIP")
        self.open_button = QPushButton("打开输出目录")
        self.install_button = QPushButton("安装到当前整合包")
        self.install_button.setProperty("variant", "primary")
        actions.addWidget(self.back_button)
        actions.addStretch()
        actions.addWidget(self.open_button)
        actions.addWidget(self.export_button)
        actions.addWidget(self.install_button)
        actions.addWidget(self.build_button)
        layout.addLayout(actions)
        layout.addStretch()
        self.setWidget(content)

        self.back_button.clicked.connect(self.back_requested)
        self.build_button.clicked.connect(self.build_requested)
        self.export_button.clicked.connect(self.export_requested)
        self.open_button.clicked.connect(self.open_requested)
        self.install_button.clicked.connect(self.install_requested)

    def show_ready(self, view_model: BuildInstallPageViewModel) -> None:
        self._render(view_model)
        self.status_title.setText("等待生成")
        self.status_detail.setText(
            "构建在后台进行，不会修改原始 mods/*.jar。"
        )
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.feedback_label.hide()
        self.back_button.setEnabled(True)
        self.build_button.setEnabled(True)
        self.open_button.setEnabled(True)
        self.export_button.setEnabled(False)
        self.install_button.setEnabled(False)

    def show_running(self, operation: str) -> None:
        self.status_title.setText(operation)
        self.status_detail.setText(
            "操作正在后台执行，窗口可以继续响应。"
        )
        self.progress_bar.setRange(0, 0)
        self.back_button.setEnabled(False)
        self.build_button.setEnabled(False)
        self.open_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.install_button.setEnabled(False)
        self.feedback_label.hide()

    def show_result(self, view_model: BuildInstallPageViewModel) -> None:
        self._render(view_model)
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(1)
        self.back_button.setEnabled(True)
        self.build_button.setEnabled(True)
        self.open_button.setEnabled(True)
        self.export_button.setEnabled(view_model.can_export)
        self.install_button.setEnabled(view_model.can_install)
        if view_model.errors:
            self.status_title.setText("生成完成，但检查未通过")
            self.status_detail.setText(
                "请修复错误后重新生成；当前结果不会自动安装。"
            )
        else:
            self.status_title.setText("资源包生成完成")
            self.status_detail.setText(
                "可以导出 ZIP，或安装到当前整合包。"
            )

    def show_failure(self, message: str) -> None:
        self.status_title.setText("操作失败")
        self.status_detail.setText(
            "已有成功输出不会被主动删除，可以修复问题后重试。"
        )
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.feedback_label.setProperty("tone", "error")
        self.feedback_label.setText(message)
        self.feedback_label.show()
        self.back_button.setEnabled(True)
        self.build_button.setEnabled(True)
        self.open_button.setEnabled(True)
        self.export_button.setEnabled(False)
        self.install_button.setEnabled(False)

    def show_feedback(self, message: str, *, error: bool = False) -> None:
        self.feedback_label.setProperty(
            "tone",
            "error" if error else "success",
        )
        self.feedback_label.setText(message)
        self.feedback_label.show()
        self.feedback_label.style().unpolish(self.feedback_label)
        self.feedback_label.style().polish(self.feedback_label)

    def _render(self, view_model: BuildInstallPageViewModel) -> None:
        self.summary_values["name"].setText(view_model.output_name)
        self.summary_values["entries"].setText(view_model.entry_text)
        self.summary_values["resource"].setText(view_model.resource_text)
        self.summary_values["config"].setText(view_model.config_text)
        self.summary_values["install"].setText(view_model.install_text)
        self.summary_values["pack_format"].setText(
            view_model.pack_format_text
        )
        self.output_directory_label.setText(view_model.output_directory)
        diagnostics = [
            *(f"错误：{item}" for item in view_model.errors),
            *(f"警告：{item}" for item in view_model.warnings),
        ]
        self.diagnostics_label.setText(
            "\n".join(diagnostics) if diagnostics else "未发现问题"
        )


