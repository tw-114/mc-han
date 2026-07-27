from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from PySide6.QtCore import QThreadPool, Qt, Slot
from PySide6.QtGui import QCloseEvent, QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from mc_han.qt.pages.home_page import HomePage
from mc_han.qt.pages.inspection_page import InspectionPage
from mc_han.qt.task_runner import InspectionTask, TaskFailure
from mc_han.qt.theme import application_stylesheet
from mc_han.qt.view_models import InspectionPageViewModel, WorkflowStage
from mc_han.services.modpack_inspector import inspect_modpack
from mc_han.workflow.models import ModpackInspection


DirectoryPicker = Callable[[], str | None]
InspectionService = Callable[[Path], ModpackInspection]


class MainWindow(QMainWindow):
    def __init__(
        self,
        *,
        inspection_service: InspectionService = inspect_modpack,
        directory_picker: DirectoryPicker | None = None,
    ) -> None:
        super().__init__()
        self._inspection_service = inspection_service
        self._directory_picker = directory_picker or self._choose_directory
        self._thread_pool = QThreadPool(self)
        self._thread_pool.setMaxThreadCount(1)
        self._active_task: InspectionTask | None = None
        self._inspection_running = False
        self.current_inspection: ModpackInspection | None = None
        self.stage = WorkflowStage.WELCOME

        self.setWindowTitle("mc-han")
        self.resize(1120, 720)
        self.setMinimumSize(920, 620)
        self.setStyleSheet(application_stylesheet())

        root = QWidget()
        root.setObjectName("AppRoot")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self._build_top_bar())

        self.pages = QStackedWidget()
        self.home_page = HomePage()
        self.inspection_page = InspectionPage()
        self.scan_placeholder_page = self._build_scan_placeholder()
        self.pages.addWidget(self.home_page)
        self.pages.addWidget(self.inspection_page)
        self.pages.addWidget(self.scan_placeholder_page)
        root_layout.addWidget(self.pages, stretch=1)
        root_layout.addWidget(self._build_footer())
        self.setCentralWidget(root)

        self.home_page.select_requested.connect(self.choose_and_inspect)
        self.inspection_page.reselect_requested.connect(self.choose_and_inspect)
        self.inspection_page.home_requested.connect(self.show_home)
        self.inspection_page.scan_requested.connect(self.show_scan_placeholder)
        self.home_nav_button.clicked.connect(self.show_home)
        self.show_home()

    def _build_top_bar(self) -> QFrame:
        top_bar = QFrame()
        top_bar.setObjectName("TopBar")
        top_bar.setFixedHeight(66)
        layout = QHBoxLayout(top_bar)
        layout.setContentsMargins(28, 0, 28, 0)
        layout.setSpacing(8)

        brand = QLabel("mc-han")
        brand.setObjectName("BrandLabel")
        layout.addWidget(brand)
        layout.addSpacing(18)

        self.home_nav_button = self._nav_button("首页", enabled=True)
        self.home_nav_button.setCheckable(True)
        layout.addWidget(self.home_nav_button)
        for label in ("项目", "汉化", "检查", "设置"):
            layout.addWidget(self._nav_button(label, enabled=False))
        layout.addStretch()

        self.project_label = QLabel("未选择项目")
        self.project_label.setObjectName("MutedLabel")
        layout.addWidget(self.project_label)
        theme_button = QPushButton("主题")
        theme_button.setEnabled(False)
        theme_button.setToolTip("深色模式将在后续版本开放")
        layout.addWidget(theme_button)
        return top_bar

    def _build_footer(self) -> QFrame:
        footer = QFrame()
        footer.setObjectName("FooterBar")
        footer.setFixedHeight(38)
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(28, 0, 28, 0)
        self.footer_status = QLabel("准备就绪")
        self.footer_status.setObjectName("MutedLabel")
        layout.addWidget(self.footer_status)
        layout.addStretch()
        readonly = QLabel("JAR 只读")
        readonly.setProperty("tone", "success")
        layout.addWidget(readonly)
        return footer

    def _build_scan_placeholder(self) -> QWidget:
        page = QWidget()
        page.setObjectName("AppRoot")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.addStretch()
        title = QLabel("整合包检测已完成")
        title.setObjectName("PageTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        detail = QLabel("扫描分类功能将在下一批接入。")
        detail.setObjectName("MutedLabel")
        detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        back_button = QPushButton("返回检测结果")
        back_button.clicked.connect(self.show_inspection_result)
        layout.addWidget(title)
        layout.addWidget(detail)
        layout.addWidget(back_button, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addStretch()
        return page

    @staticmethod
    def _nav_button(label: str, *, enabled: bool) -> QPushButton:
        button = QPushButton(label)
        button.setProperty("nav", True)
        button.setEnabled(enabled)
        if not enabled:
            button.setToolTip("将在后续版本开放")
        return button

    @Slot()
    def choose_and_inspect(self) -> None:
        if self._inspection_running:
            return
        selected = self._directory_picker()
        if selected:
            self.start_inspection(Path(selected))

    def start_inspection(self, path: Path) -> None:
        if self._inspection_running:
            return
        self._inspection_running = True
        self.stage = WorkflowStage.INSPECTING
        self.home_page.select_button.setEnabled(False)
        self.inspection_page.show_loading()
        self.pages.setCurrentWidget(self.inspection_page)
        self.home_nav_button.setChecked(False)
        self.footer_status.setText("正在检测整合包")

        task = InspectionTask(path, self._inspection_service)
        task.signals.completed.connect(self._inspection_completed)
        task.signals.failed.connect(self._inspection_failed)
        self._active_task = task
        self._thread_pool.start(task)

    @Slot(object)
    def _inspection_completed(self, inspection: ModpackInspection) -> None:
        self._inspection_running = False
        self._active_task = None
        self.current_inspection = inspection
        self.stage = WorkflowStage.INSPECTION_RESULT
        self.home_page.select_button.setEnabled(True)
        self.project_label.setText(inspection.display_name or "未选择项目")
        self.footer_status.setText("检测完成")
        self.inspection_page.show_result(
            InspectionPageViewModel.from_inspection(inspection)
        )

    @Slot(object)
    def _inspection_failed(self, failure: TaskFailure) -> None:
        self._inspection_running = False
        self._active_task = None
        self.stage = WorkflowStage.INSPECTION_RESULT
        self.home_page.select_button.setEnabled(True)
        self.footer_status.setText("检测未完成")
        self.inspection_page.show_failure(failure)

    @Slot()
    def show_home(self) -> None:
        if self._inspection_running:
            return
        self.stage = WorkflowStage.WELCOME
        self.pages.setCurrentWidget(self.home_page)
        self.home_nav_button.setChecked(True)
        self.footer_status.setText("准备就绪")

    @Slot()
    def show_inspection_result(self) -> None:
        if self.current_inspection is None:
            self.show_home()
            return
        self.stage = WorkflowStage.INSPECTION_RESULT
        self.pages.setCurrentWidget(self.inspection_page)
        self.home_nav_button.setChecked(False)

    @Slot()
    def show_scan_placeholder(self) -> None:
        if self.current_inspection is None or not self.current_inspection.can_continue:
            return
        self.stage = WorkflowStage.SCAN_PLACEHOLDER
        self.pages.setCurrentWidget(self.scan_placeholder_page)
        self.home_nav_button.setChecked(False)
        self.footer_status.setText("等待扫描分类功能")

    def _choose_directory(self) -> str | None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "选择 Minecraft 整合包目录",
            "",
            QFileDialog.Option.ShowDirsOnly,
        )
        return selected or None

    def closeEvent(self, event: QCloseEvent) -> None:
        self._thread_pool.waitForDone(3000)
        super().closeEvent(event)


def run_qt_app(argv: Sequence[str] | None = None) -> int:
    application = QApplication.instance()
    owns_application = application is None
    if application is None:
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
        application = QApplication(list(argv) if argv is not None else sys.argv)
    application.setApplicationName("mc-han")
    application.setOrganizationName("mc-han")
    window = MainWindow()
    window.show()
    if not owns_application:
        return 0
    return application.exec()
