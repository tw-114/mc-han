from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from PySide6.QtCore import QThreadPool, QTimer, Qt, Slot
from PySide6.QtGui import QCloseEvent, QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from mc_han.qt.pages.home_page import HomePage
from mc_han.qt.pages.inspection_page import InspectionPage
from mc_han.qt.pages.scan_page import ScanPage
from mc_han.qt.pages.translation_config_page import TranslationConfigPage
from mc_han.qt.pages.trial_translation_placeholder_page import (
    TrialTranslationPlaceholderPage,
)
from mc_han.qt.scan_view_models import ScanPageViewModel, ScanProgressViewModel
from mc_han.qt.task_runner import InspectionTask, ScanTask, TaskFailure
from mc_han.qt.theme import application_stylesheet
from mc_han.qt.translation_config_view_models import (
    TranslationConfigPageViewModel,
    TranslationProvider,
    TranslationSessionConfig,
    recommended_translation_config,
    validate_translation_config,
    with_recommended_values,
)
from mc_han.qt.view_models import InspectionPageViewModel, WorkflowStage
from mc_han.release_info import about_text
from mc_han.services.modpack_inspector import inspect_modpack
from mc_han.services.scan_service import scan_and_classify
from mc_han.version import UNKNOWN_VERSION, get_version
from mc_han.workflow.models import ModpackInspection
from mc_han.workflow.scan_models import (
    ScanCategoryId,
    ScanClassificationResult,
    ScanProgressEvent,
    ScanSelectionState,
)


DirectoryPicker = Callable[[], str | None]
InspectionService = Callable[[Path], ModpackInspection]
ScanService = Callable[..., ScanClassificationResult]


class MainWindow(QMainWindow):
    def __init__(
        self,
        *,
        inspection_service: InspectionService = inspect_modpack,
        scan_service: ScanService = scan_and_classify,
        directory_picker: DirectoryPicker | None = None,
    ) -> None:
        super().__init__()
        self._inspection_service = inspection_service
        self._scan_service = scan_service
        self._directory_picker = directory_picker or self._choose_directory
        self._thread_pool = QThreadPool(self)
        self._thread_pool.setMaxThreadCount(1)
        self._active_task: InspectionTask | ScanTask | None = None
        self._inspection_running = False
        self._scan_running = False
        self._close_when_idle = False
        self._close_scheduled = False
        self.current_inspection: ModpackInspection | None = None
        self.current_scan_result: ScanClassificationResult | None = None
        self.scan_selection: ScanSelectionState | None = None
        self.translation_config_draft: TranslationSessionConfig | None = None
        self.translation_session_config: TranslationSessionConfig | None = None
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
        self.scan_page = ScanPage()
        self.translation_config_page = TranslationConfigPage()
        self.trial_translation_page = TrialTranslationPlaceholderPage()
        self.pages.addWidget(self.home_page)
        self.pages.addWidget(self.inspection_page)
        self.pages.addWidget(self.scan_page)
        self.pages.addWidget(self.translation_config_page)
        self.pages.addWidget(self.trial_translation_page)
        root_layout.addWidget(self.pages, stretch=1)
        root_layout.addWidget(self._build_footer())
        self.setCentralWidget(root)

        self.home_page.select_requested.connect(self.choose_and_inspect)
        self.inspection_page.reselect_requested.connect(self.choose_and_inspect)
        self.inspection_page.home_requested.connect(self.show_home)
        self.inspection_page.scan_requested.connect(self.start_scan)
        self.scan_page.back_requested.connect(self.show_inspection_result)
        self.scan_page.rescan_requested.connect(self.start_scan)
        self.scan_page.continue_requested.connect(self.show_translation_config)
        self.scan_page.select_all_requested.connect(self.select_all_scan_categories)
        self.scan_page.clear_selection_requested.connect(
            self.clear_scan_categories
        )
        self.scan_page.restore_defaults_requested.connect(
            self.restore_scan_category_defaults
        )
        self.scan_page.category_toggled.connect(self.set_scan_category_selected)
        self.translation_config_page.back_requested.connect(
            self.return_to_scan_from_translation_config
        )
        self.translation_config_page.restore_requested.connect(
            self.restore_recommended_translation_config
        )
        self.translation_config_page.validate_requested.connect(
            self.validate_current_translation_config
        )
        self.translation_config_page.continue_requested.connect(
            self.save_translation_config_and_continue
        )
        self.translation_config_page.provider_changed.connect(
            self.change_translation_provider
        )
        self.trial_translation_page.back_requested.connect(
            self.show_translation_config
        )
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
        about_button = QPushButton("关于")
        about_button.setToolTip("查看版本和开源信息")
        about_button.clicked.connect(self.show_about)
        layout.addWidget(about_button)
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
        version_label = QLabel(f"v{get_version()}")
        version_label.setObjectName("MutedLabel")
        layout.addWidget(version_label)
        readonly = QLabel("JAR 只读")
        readonly.setProperty("tone", "success")
        layout.addWidget(readonly)
        return footer

    @Slot()
    def show_about(self) -> None:
        QMessageBox.information(self, "关于 mc-han", about_text())

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
        if self._close_when_idle or self._inspection_running or self._scan_running:
            return
        selected = self._directory_picker()
        if selected:
            self.start_inspection(Path(selected))

    def start_inspection(self, path: Path) -> None:
        if self._close_when_idle or self._inspection_running or self._scan_running:
            return
        self._inspection_running = True
        self.current_scan_result = None
        self.scan_selection = None
        self.translation_config_draft = None
        self.translation_session_config = None
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
        self._close_if_pending()

    @Slot(object)
    def _inspection_failed(self, failure: TaskFailure) -> None:
        self._inspection_running = False
        self._active_task = None
        self.stage = WorkflowStage.INSPECTION_RESULT
        self.home_page.select_button.setEnabled(True)
        self.footer_status.setText("检测未完成")
        self.inspection_page.show_failure(failure)
        self._close_if_pending()

    @Slot()
    def start_scan(self) -> None:
        if self._close_when_idle or self._inspection_running or self._scan_running:
            return
        inspection = self.current_inspection
        if inspection is None or not inspection.can_continue:
            return
        self._scan_running = True
        self.current_scan_result = None
        self.scan_selection = None
        self.stage = WorkflowStage.SCANNING
        self.scan_page.show_loading(inspection.display_name)
        self.pages.setCurrentWidget(self.scan_page)
        self.home_nav_button.setChecked(False)
        self.footer_status.setText("正在扫描整合包")

        task = ScanTask(
            inspection.input_directory,
            self._scan_service,
            inspection.existing_chinese,
        )
        task.signals.progress.connect(self._scan_progress)
        task.signals.completed.connect(self._scan_completed)
        task.signals.failed.connect(self._scan_failed)
        self._active_task = task
        self._thread_pool.start(task)

    @Slot(object)
    def _scan_progress(self, event: ScanProgressEvent) -> None:
        if not self._scan_running:
            return
        self.scan_page.update_progress(ScanProgressViewModel.from_event(event))

    @Slot(object)
    def _scan_completed(self, result: ScanClassificationResult) -> None:
        self._scan_running = False
        self._active_task = None
        self.current_scan_result = result
        self.scan_selection = ScanSelectionState.from_result(result)
        self.stage = WorkflowStage.SCAN_RESULT
        self.footer_status.setText("扫描与分类完成")
        self._render_scan_result()
        self._close_if_pending()

    @Slot(object)
    def _scan_failed(self, failure: TaskFailure) -> None:
        self._scan_running = False
        self._active_task = None
        self.stage = WorkflowStage.SCAN_RESULT
        self.footer_status.setText("扫描未完成")
        self.scan_page.show_failure(failure)
        self._close_if_pending()

    @Slot(object, bool)
    def set_scan_category_selected(
        self,
        category_id: ScanCategoryId,
        selected: bool,
    ) -> None:
        if self.scan_selection is None:
            return
        self.scan_selection = self.scan_selection.set_selected(
            category_id,
            selected,
        )
        self._render_scan_result()

    @Slot()
    def select_all_scan_categories(self) -> None:
        if self.scan_selection is not None:
            self.scan_selection = self.scan_selection.select_all()
            self._render_scan_result()

    @Slot()
    def clear_scan_categories(self) -> None:
        if self.scan_selection is not None:
            self.scan_selection = self.scan_selection.clear()
            self._render_scan_result()

    @Slot()
    def restore_scan_category_defaults(self) -> None:
        if self.scan_selection is not None:
            self.scan_selection = self.scan_selection.restore_defaults()
            self._render_scan_result()

    def _render_scan_result(self) -> None:
        if self.scan_selection is None or self.current_inspection is None:
            return
        self.scan_page.show_result(
            ScanPageViewModel.from_selection(
                self.current_inspection.display_name,
                self.scan_selection,
            )
        )
        self.pages.setCurrentWidget(self.scan_page)
        self.home_nav_button.setChecked(False)

    @Slot()
    def show_home(self) -> None:
        if self._inspection_running or self._scan_running:
            return
        self.stage = WorkflowStage.WELCOME
        self.pages.setCurrentWidget(self.home_page)
        self.home_nav_button.setChecked(True)
        self.footer_status.setText("准备就绪")

    @Slot()
    def show_inspection_result(self) -> None:
        if self._scan_running:
            return
        if self.current_inspection is None:
            self.show_home()
            return
        self.stage = WorkflowStage.INSPECTION_RESULT
        self.pages.setCurrentWidget(self.inspection_page)
        self.home_nav_button.setChecked(False)

    @Slot()
    def show_scan_result(self) -> None:
        if self._scan_running:
            return
        if self.scan_selection is None:
            self.show_inspection_result()
            return
        self.stage = WorkflowStage.SCAN_RESULT
        self._render_scan_result()

    @Slot()
    def show_translation_config(self) -> None:
        if self.scan_selection is None or self.scan_selection.selected_record_count == 0:
            return
        config = (
            self.translation_config_draft
            or self.translation_session_config
            or recommended_translation_config()
        )
        self.translation_config_page.show_config(
            TranslationConfigPageViewModel(
                config=config,
                selected_record_count=self.scan_selection.selected_record_count,
                selected_category_count=len(
                    self.scan_selection.selected_category_ids
                ),
            )
        )
        self.stage = WorkflowStage.TRANSLATION_CONFIG
        self.pages.setCurrentWidget(self.translation_config_page)
        self.home_nav_button.setChecked(False)
        self.footer_status.setText("配置翻译服务")

    @Slot()
    def return_to_scan_from_translation_config(self) -> None:
        self.translation_config_draft = (
            self.translation_config_page.current_config()
        )
        self.show_scan_result()

    @Slot(object)
    def change_translation_provider(
        self,
        provider: TranslationProvider,
    ) -> None:
        current = self.translation_config_page.current_config()
        updated = with_recommended_values(current, provider)
        self.translation_config_draft = updated
        self.translation_config_page.set_config(updated)
        self.translation_config_page.clear_validation()

    @Slot()
    def restore_recommended_translation_config(self) -> None:
        current = self.translation_config_page.current_config()
        updated = with_recommended_values(current)
        self.translation_config_draft = updated
        self.translation_config_page.set_config(updated)
        self.translation_config_page.clear_validation()

    @Slot()
    def validate_current_translation_config(self) -> None:
        config = self.translation_config_page.current_config()
        self.translation_config_draft = config
        self.translation_config_page.show_validation(
            validate_translation_config(config)
        )

    @Slot()
    def save_translation_config_and_continue(self) -> None:
        config = self.translation_config_page.current_config()
        validation = validate_translation_config(config)
        self.translation_config_draft = config
        self.translation_config_page.show_validation(validation)
        if not validation.valid:
            return
        self.translation_session_config = config
        self.stage = WorkflowStage.TRIAL_TRANSLATION_PLACEHOLDER
        self.pages.setCurrentWidget(self.trial_translation_page)
        self.home_nav_button.setChecked(False)
        self.footer_status.setText("翻译服务配置完成")

    def _choose_directory(self) -> str | None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "选择 Minecraft 整合包目录",
            "",
            QFileDialog.Option.ShowDirsOnly,
        )
        return selected or None

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._inspection_running or self._scan_running:
            event.ignore()
            if not self._close_when_idle:
                self._close_when_idle = True
                self._disable_task_starters_for_pending_close()
                self.footer_status.setText(
                    "正在安全结束当前扫描，完成后将自动关闭"
                )
            # This keeps the event loop responsive until the read-only worker
            # returns. A future batch can add cooperative cancellation checks;
            # a thread blocked in operating-system I/O cannot be cancelled here.
            return
        self._close_when_idle = False
        self._close_scheduled = False
        super().closeEvent(event)

    def _disable_task_starters_for_pending_close(self) -> None:
        self.home_page.select_button.setEnabled(False)
        self.inspection_page.reselect_button.setEnabled(False)
        self.inspection_page.start_scan_button.setEnabled(False)
        self.scan_page.rescan_button.setEnabled(False)
        self.scan_page.continue_button.setEnabled(False)

    def _close_if_pending(self) -> None:
        if not self._close_when_idle or self._close_scheduled:
            return
        self._close_scheduled = True
        self._disable_task_starters_for_pending_close()
        self.footer_status.setText(
            "正在安全结束当前扫描，完成后将自动关闭"
        )
        QTimer.singleShot(0, self.close)


def run_qt_app(
    argv: Sequence[str] | None = None,
    *,
    smoke_test: bool = False,
) -> int:
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

    smoke_state = {"completed": not smoke_test, "valid": not smoke_test}
    if smoke_test:
        def complete_smoke_test() -> None:
            smoke_state["completed"] = True
            smoke_state["valid"] = (
                window.isVisible()
                and window.home_page.select_button.isEnabled()
                and window.pages.currentWidget() is window.home_page
                and get_version() != UNKNOWN_VERSION
            )
            window.close()
            application.quit()

        QTimer.singleShot(250, complete_smoke_test)

    exit_code = application.exec()
    if smoke_test and (not smoke_state["completed"] or not smoke_state["valid"]):
        return 1
    return exit_code
