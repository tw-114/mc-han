from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from mc_han.qt.widgets.card import Card
from mc_han.services.project_discovery import DiscoveredProject
from mc_han.services.recent_projects import RecentProject


class HomePage(QScrollArea):
    select_requested = Signal()
    project_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("HomePage")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)

        content = QWidget()
        content.setObjectName("AppRoot")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(32, 28, 32, 32)
        layout.setSpacing(18)

        title = QLabel("让 Minecraft 整合包汉化变得简单")
        title.setObjectName("PageTitle")
        subtitle = QLabel(
            "自动检测、AI 翻译、质量检查和安全安装。\n"
            "不会修改 mods 文件夹中的任何 JAR。"
        )
        subtitle.setObjectName("MutedLabel")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        select_card = Card()
        select_title = QLabel("选择一个整合包")
        select_title.setObjectName("SectionTitle")
        select_hint = QLabel(
            "请选择实例根目录。通常可以在其中看到 mods、config、"
            "resourcepacks 或实例元数据文件。"
        )
        select_hint.setObjectName("MutedLabel")
        select_hint.setWordWrap(True)
        self.select_button = QPushButton("选择整合包")
        self.select_button.setObjectName("SelectModpackButton")
        self.select_button.setProperty("variant", "primary")
        self.select_button.setMinimumWidth(160)
        self.select_button.clicked.connect(lambda: self.select_requested.emit())
        self.continue_button = QPushButton("继续上次项目")
        self.continue_button.setProperty("variant", "primary")
        self.continue_button.setVisible(False)
        self._continue_path = ""
        self.continue_button.clicked.connect(self._emit_continue_project)
        select_card.content_layout.addWidget(select_title)
        select_card.content_layout.addWidget(select_hint)
        select_card.content_layout.addWidget(
            self.continue_button,
            alignment=Qt.AlignmentFlag.AlignLeft,
        )
        select_card.content_layout.addWidget(
            self.select_button,
            alignment=Qt.AlignmentFlag.AlignLeft,
        )
        layout.addWidget(select_card)

        workflow_title = QLabel("从整合包到可安装汉化包")
        workflow_title.setObjectName("SectionTitle")
        layout.addWidget(workflow_title)
        workflow_card = Card()
        workflow_text = QLabel(
            "1. 整合包  →  2. 汉化  →  3. 安装"
        )
        workflow_text.setObjectName("ValueLabel")
        workflow_text.setWordWrap(True)
        workflow_hint = QLabel(
            "选择整合包后自动检测并扫描；汉化阶段包含方案、试译、完整翻译"
            "和检查；安装阶段负责生成、导出、安装与撤销。"
        )
        workflow_hint.setObjectName("MutedLabel")
        workflow_hint.setWordWrap(True)
        workflow_card.content_layout.addWidget(workflow_text)
        workflow_card.content_layout.addWidget(workflow_hint)
        layout.addWidget(workflow_card)

        safety_title = QLabel("检测过程保持只读")
        safety_title.setObjectName("SectionTitle")
        layout.addWidget(safety_title)
        safety_row = QHBoxLayout()
        safety_row.setSpacing(14)
        for heading, detail in (
            ("JAR 始终只读", "mods/*.jar 不会被修改或重新打包。"),
            ("不会调用 API", "检测阶段不会连接任何翻译服务。"),
            ("不会生成扫描文件", "不会创建 CSV 或翻译缓存。"),
            ("只读取必要信息", "仅读取元数据和 JAR entry 名称。"),
        ):
            card = Card()
            card.setMinimumWidth(190)
            card_title = QLabel(heading)
            card_title.setObjectName("CardTitle")
            card_detail = QLabel(detail)
            card_detail.setObjectName("MutedLabel")
            card_detail.setWordWrap(True)
            card.content_layout.addWidget(card_title)
            card.content_layout.addWidget(card_detail)
            card.content_layout.addStretch()
            safety_row.addWidget(card)
        layout.addLayout(safety_row)

        recent_title = QLabel("最近项目")
        recent_title.setObjectName("SectionTitle")
        layout.addWidget(recent_title)
        self.recent_card = Card()
        self.recent_empty = QLabel("尚无最近项目")
        self.recent_empty.setObjectName("CardTitle")
        self.recent_hint = QLabel("检测过的整合包会显示在这里，下次可直接继续。")
        self.recent_hint.setObjectName("MutedLabel")
        self.recent_hint.setWordWrap(True)
        self.recent_list = QVBoxLayout()
        self.recent_list.setSpacing(8)
        self.recent_card.content_layout.addWidget(self.recent_empty)
        self.recent_card.content_layout.addWidget(self.recent_hint)
        self.recent_card.content_layout.addLayout(self.recent_list)
        layout.addWidget(self.recent_card)

        discovered_title = QLabel("自动发现")
        discovered_title.setObjectName("SectionTitle")
        layout.addWidget(discovered_title)
        self.discovered_card = Card()
        self.discovered_empty = QLabel("未在常见启动器目录中发现整合包")
        self.discovered_empty.setObjectName("MutedLabel")
        self.discovered_empty.setWordWrap(True)
        self.discovered_list = QVBoxLayout()
        self.discovered_list.setSpacing(8)
        self.discovered_card.content_layout.addWidget(self.discovered_empty)
        self.discovered_card.content_layout.addLayout(self.discovered_list)
        layout.addWidget(self.discovered_card)
        layout.addStretch()

        self.setWidget(content)

    def show_projects(
        self,
        recent: tuple[RecentProject, ...],
        discovered: tuple[DiscoveredProject, ...],
    ) -> None:
        self._clear_layout(self.recent_list)
        self._clear_layout(self.discovered_list)
        self.recent_empty.setVisible(not recent)
        self.recent_hint.setVisible(not recent)
        self.discovered_empty.setVisible(not discovered)

        self.continue_button.setVisible(bool(recent))
        if recent:
            latest = recent[0]
            self._continue_path = str(latest.path)
            self.continue_button.setText(f"继续：{latest.display_name}")
            self.continue_button.setToolTip(str(latest.path))
        else:
            self._continue_path = ""
        for project in recent:
            loader = project.loader_name
            if project.loader_version != "unknown":
                loader = f"{loader} {project.loader_version}"
            detail = " · ".join(
                value
                for value in (
                    (
                        project.minecraft_version
                        if project.minecraft_version != "unknown"
                        else "Minecraft 未识别"
                    ),
                    loader if loader != "unknown" else "Loader 未识别",
                    self._progress_label(project.current_stage),
                )
                if value
            )
            self.recent_list.addWidget(
                self._project_row(
                    project.display_name,
                    detail,
                    project.path,
                )
            )
        recent_paths = {
            self._path_identity(project.path) for project in recent
        }
        for project in discovered:
            if self._path_identity(project.path) in recent_paths:
                continue
            self.discovered_list.addWidget(
                self._project_row(
                    project.display_name,
                    project.launcher,
                    project.path,
                )
            )

    def _project_row(self, title: str, detail: str, path: object) -> QFrame:
        row = QFrame()
        row.setObjectName("ProjectRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 6, 0, 6)
        labels = QVBoxLayout()
        heading = QLabel(title)
        heading.setObjectName("CardTitle")
        description = QLabel(detail)
        description.setObjectName("MutedLabel")
        description.setWordWrap(True)
        labels.addWidget(heading)
        labels.addWidget(description)
        button = QPushButton("打开")
        button.setToolTip(str(path))
        button.clicked.connect(
            lambda _checked=False, selected=path: (
                self.project_requested.emit(str(selected))
            )
        )
        layout.addLayout(labels, stretch=1)
        layout.addWidget(button)
        return row

    def _emit_continue_project(self) -> None:
        if self._continue_path:
            self.project_requested.emit(self._continue_path)

    @staticmethod
    def _clear_layout(layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    @staticmethod
    def _progress_label(stage: str) -> str:
        labels = {
            "welcome": "待检测",
            "inspecting": "正在检测",
            "inspection_result": "检测完成",
            "scanning": "正在扫描",
            "scan_result": "扫描完成",
            "translation_config": "配置翻译",
            "trial_translation": "试译",
            "full_translation": "完整翻译",
            "translation_review": "检查译文",
            "build_install": "构建与安装",
            "completion": "已完成",
        }
        return labels.get(stage, "可继续")

    @staticmethod
    def _path_identity(path: object) -> str:
        return str(path).replace("\\", "/").casefold()
