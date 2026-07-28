from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from PySide6.QtCore import QThreadPool, QTimer, Qt, QUrl, Slot
from PySide6.QtGui import QCloseEvent, QDesktopServices, QGuiApplication
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

from mc_han.core.project import project_paths
from mc_han.csv_store import read_extracted_csv
from mc_han.builder.installer import InstallResult, RollbackResult
from mc_han.qt.build_install_view_models import (
    BuildInstallPageViewModel,
    CompletionPageViewModel,
)
from mc_han.qt.full_translation_view_models import (
    FullTranslationPageViewModel,
)
from mc_han.qt.pages.build_install_page import BuildInstallPage
from mc_han.qt.pages.completion_page import CompletionPage
from mc_han.qt.pages.full_translation_page import FullTranslationPage
from mc_han.qt.pages.home_page import HomePage
from mc_han.qt.pages.inspection_page import InspectionPage
from mc_han.qt.pages.scan_page import ScanPage
from mc_han.qt.pages.translation_config_page import TranslationConfigPage
from mc_han.qt.pages.trial_translation_page import TrialTranslationPage
from mc_han.qt.pages.translation_review_page import TranslationReviewPage
from mc_han.qt.scan_view_models import ScanPageViewModel, ScanProgressViewModel
from mc_han.qt.task_runner import (
    BuildTask,
    ExportTask,
    InspectionTask,
    FullTranslationTask,
    FullTranslationTaskResult,
    InstallTask,
    RollbackTask,
    ScanTask,
    TaskFailure,
    TrialTranslationTask,
)
from mc_han.qt.theme import application_stylesheet
from mc_han.qt.translation_config_view_models import (
    TranslationConfigPageViewModel,
    TranslationProvider,
    TranslationSessionConfig,
    recommended_translation_config,
    validate_translation_config,
    with_recommended_values,
    create_translator,
)
from mc_han.qt.trial_view_models import TrialPageViewModel
from mc_han.qt.translation_review_view_models import (
    TranslationReviewPageViewModel,
)
from mc_han.qt.view_models import InspectionPageViewModel, WorkflowStage
from mc_han.release_info import about_text
from mc_han.services.modpack_inspector import inspect_modpack
from mc_han.services.build_install import (
    BuildWorkflowResult,
    ExportWorkflowResult,
    build_localization_package,
    export_localization_zip,
    install_localization_package,
    rollback_localization_install,
)
from mc_han.services.scan_service import scan_and_classify
from mc_han.services.trial_translation import (
    TrialTranslationError,
    prepare_trial_samples,
    run_trial_translation,
)
from mc_han.services.translation_review import (
    ReviewAction,
    load_translation_review,
    update_translation_review_record,
)
from mc_han.version import UNKNOWN_VERSION, get_version
from mc_han.workflow.models import ModpackInspection
from mc_han.workflow.scan_models import (
    CATEGORY_DEFINITIONS,
    SOURCE_TYPE_TO_CATEGORY,
    ScanCategoryId,
    ScanClassificationResult,
    ScanProgressEvent,
    ScanSelectionState,
)
from mc_han.translator.engine import TranslationProgress, TranslationStarted
from mc_han.usage.models import TranslationUsageSummary
from mc_han.workflow.trial_models import (
    TrialProgressEvent,
    TrialSampleResult,
    TrialTranslationResult,
)


DirectoryPicker = Callable[[], str | None]
InspectionService = Callable[[Path], ModpackInspection]
ScanService = Callable[..., ScanClassificationResult]
TrialPrepareService = Callable[
    [Path, ScanSelectionState],
    tuple[TrialSampleResult, ...],
]
TrialService = Callable[..., TrialTranslationResult]
FullTranslatorFactory = Callable[[TranslationSessionConfig], object]
BuildService = Callable[..., BuildWorkflowResult]
ExportService = Callable[..., ExportWorkflowResult]
InstallService = Callable[..., InstallResult]
RollbackService = Callable[..., RollbackResult]
DirectoryOpener = Callable[[Path], bool]


class MainWindow(QMainWindow):
    def __init__(
        self,
        *,
        inspection_service: InspectionService = inspect_modpack,
        scan_service: ScanService = scan_and_classify,
        trial_prepare_service: TrialPrepareService = prepare_trial_samples,
        trial_service: TrialService = run_trial_translation,
        full_translator_factory: FullTranslatorFactory = create_translator,
        build_service: BuildService = build_localization_package,
        export_service: ExportService = export_localization_zip,
        install_service: InstallService = install_localization_package,
        rollback_service: RollbackService = rollback_localization_install,
        directory_opener: DirectoryOpener | None = None,
        directory_picker: DirectoryPicker | None = None,
    ) -> None:
        super().__init__()
        self._inspection_service = inspection_service
        self._scan_service = scan_service
        self._trial_prepare_service = trial_prepare_service
        self._trial_service = trial_service
        self._full_translator_factory = full_translator_factory
        self._build_service = build_service
        self._export_service = export_service
        self._install_service = install_service
        self._rollback_service = rollback_service
        self._directory_opener = directory_opener or self._open_directory
        self._directory_picker = directory_picker or self._choose_directory
        self._thread_pool = QThreadPool(self)
        self._thread_pool.setMaxThreadCount(1)
        self._active_task: (
            InspectionTask
            | ScanTask
            | TrialTranslationTask
            | FullTranslationTask
            | BuildTask
            | ExportTask
            | InstallTask
            | RollbackTask
            | None
        ) = None
        self._inspection_running = False
        self._scan_running = False
        self._trial_running = False
        self._full_running = False
        self._build_running = False
        self._close_when_idle = False
        self._close_scheduled = False
        self.current_inspection: ModpackInspection | None = None
        self.current_scan_result: ScanClassificationResult | None = None
        self.scan_selection: ScanSelectionState | None = None
        self.translation_config_draft: TranslationSessionConfig | None = None
        self.translation_session_config: TranslationSessionConfig | None = None
        self.trial_samples: tuple[TrialSampleResult, ...] = ()
        self.trial_result: TrialTranslationResult | None = None
        self._trial_task_id = ""
        self._full_task: FullTranslationTask | None = None
        self._full_selected_ids: frozenset[str] = frozenset()
        self._full_pending_ids: frozenset[str] = frozenset()
        self._full_result: FullTranslationTaskResult | None = None
        self._full_task_id = ""
        self._full_current_category = ""
        self._full_translated_before_run = 0
        self._full_elapsed_previous = 0.0
        self._full_started_at = 0.0
        self._build_result: BuildWorkflowResult | None = None
        self._install_result: InstallResult | None = None
        self._export_result: ExportWorkflowResult | None = None
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
        self.trial_translation_page = TrialTranslationPage()
        self.full_translation_page = FullTranslationPage()
        self.translation_review_page = TranslationReviewPage()
        self.build_install_page = BuildInstallPage()
        self.completion_page = CompletionPage()
        self.pages.addWidget(self.home_page)
        self.pages.addWidget(self.inspection_page)
        self.pages.addWidget(self.scan_page)
        self.pages.addWidget(self.translation_config_page)
        self.pages.addWidget(self.trial_translation_page)
        self.pages.addWidget(self.full_translation_page)
        self.pages.addWidget(self.translation_review_page)
        self.pages.addWidget(self.build_install_page)
        self.pages.addWidget(self.completion_page)
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
        self.trial_translation_page.start_requested.connect(
            self.start_trial_translation
        )
        self.trial_translation_page.retry_requested.connect(
            self.retry_failed_trial_samples
        )
        self.trial_translation_page.continue_requested.connect(
            self.confirm_trial_translation
        )
        self.full_translation_page.back_requested.connect(
            self.show_trial_translation_result
        )
        self.full_translation_page.start_requested.connect(
            self.start_full_translation
        )
        self.full_translation_page.pause_requested.connect(
            self.pause_full_translation
        )
        self.full_translation_page.resume_requested.connect(
            self.resume_full_translation
        )
        self.full_translation_page.retry_requested.connect(
            self.retry_failed_full_translation
        )
        self.translation_review_page.back_requested.connect(
            self.show_full_translation_result
        )
        self.translation_review_page.save_requested.connect(
            self.save_review_translation
        )
        self.translation_review_page.approve_requested.connect(
            self.approve_review_record
        )
        self.translation_review_page.needs_retranslate_requested.connect(
            self.mark_review_record_for_retranslation
        )
        self.translation_review_page.skip_requested.connect(
            self.skip_review_record
        )
        self.translation_review_page.retranslate_placeholder_requested.connect(
            self.show_retranslate_placeholder
        )
        self.translation_review_page.continue_requested.connect(
            self.show_build_install
        )
        self.build_install_page.back_requested.connect(
            self.show_translation_review
        )
        self.build_install_page.build_requested.connect(self.start_build)
        self.build_install_page.export_requested.connect(self.start_export)
        self.build_install_page.open_requested.connect(
            self.open_output_directory
        )
        self.build_install_page.install_requested.connect(
            self.start_install
        )
        self.completion_page.back_requested.connect(
            self.show_build_install
        )
        self.completion_page.open_requested.connect(
            self.open_output_directory
        )
        self.completion_page.rollback_requested.connect(
            self.start_install_rollback
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
        if self._task_running() or self._close_when_idle:
            return
        selected = self._directory_picker()
        if selected:
            self.start_inspection(Path(selected))

    def start_inspection(self, path: Path) -> None:
        if self._task_running() or self._close_when_idle:
            return
        self._inspection_running = True
        self.current_scan_result = None
        self.scan_selection = None
        self.translation_config_draft = None
        self.translation_session_config = None
        self.trial_samples = ()
        self.trial_result = None
        self._trial_task_id = ""
        self._clear_full_translation_state()
        self._clear_build_state()
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
        if self._task_running() or self._close_when_idle:
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
        if self._task_running():
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
        if self.current_inspection is None or self.scan_selection is None:
            return
        try:
            samples = self._trial_prepare_service(
                self.current_inspection.input_directory,
                self.scan_selection,
            )
        except TrialTranslationError:
            self.trial_samples = ()
            self.trial_result = None
            self._trial_task_id = ""
            self.trial_translation_page.show_ready(
                TrialPageViewModel.ready(())
            )
            self.trial_translation_page.show_failure(
                "无法准备试译样本，请返回扫描页面重新扫描。",
                can_retry=False,
            )
        else:
            self.trial_samples = samples
            self.trial_result = None
            self._trial_task_id = f"trial-{uuid4().hex}"
            self.trial_translation_page.show_ready(
                TrialPageViewModel.ready(samples)
            )
        self.stage = WorkflowStage.TRIAL_TRANSLATION
        self.pages.setCurrentWidget(self.trial_translation_page)
        self.home_nav_button.setChecked(False)
        self.footer_status.setText("等待确认试译")

    @Slot()
    def start_trial_translation(self) -> None:
        self._start_trial(target_ids=None)

    @Slot()
    def retry_failed_trial_samples(self) -> None:
        if self.trial_result is None or not self.trial_result.failed_ids:
            return
        self._start_trial(target_ids=self.trial_result.failed_ids)

    def _start_trial(
        self,
        *,
        target_ids: frozenset[str] | None,
    ) -> None:
        if self._task_running() or self._close_when_idle:
            return
        if (
            self.current_inspection is None
            or self.translation_session_config is None
            or not self.trial_samples
            or not self._trial_task_id
        ):
            return
        self._trial_running = True
        self.trial_translation_page.show_running(
            retry=target_ids is not None
        )
        self.footer_status.setText("正在进行小批量试译")
        task = TrialTranslationTask(
            self.current_inspection.input_directory,
            self.translation_session_config,
            self.trial_samples,
            self._trial_service,
            task_id=self._trial_task_id,
            target_ids=target_ids,
        )
        task.signals.progress.connect(self._trial_progress)
        task.signals.completed.connect(self._trial_completed)
        task.signals.failed.connect(self._trial_failed)
        self._active_task = task
        self._thread_pool.start(task)

    @Slot(object)
    def _trial_progress(self, event: TrialProgressEvent) -> None:
        if self._trial_running:
            self.trial_translation_page.update_progress(event)

    @Slot(object)
    def _trial_completed(self, result: TrialTranslationResult) -> None:
        self._trial_running = False
        self._active_task = None
        if self.trial_result is not None:
            result = replace(
                result,
                elapsed_seconds=(
                    self.trial_result.elapsed_seconds
                    + result.elapsed_seconds
                ),
            )
        self.trial_result = result
        self.trial_samples = result.samples
        self.trial_translation_page.show_result(
            TrialPageViewModel.from_result(result)
        )
        self.footer_status.setText("试译完成")
        self._close_if_pending()

    @Slot(object)
    def _trial_failed(self, failure: TaskFailure) -> None:
        self._trial_running = False
        self._active_task = None
        can_retry = bool(
            self.trial_result is not None
            and self.trial_result.failed_ids
        )
        self.trial_translation_page.show_failure(
            failure.message,
            can_retry=can_retry,
        )
        self.footer_status.setText("试译未完成")
        self._close_if_pending()

    @Slot()
    def confirm_trial_translation(self) -> None:
        if self._trial_running or self.trial_result is None:
            return
        if self.trial_result.successful_count == 0:
            return
        if self.current_inspection is None or self.scan_selection is None:
            return
        try:
            records = read_extracted_csv(
                project_paths(
                    self.current_inspection.input_directory
                ).extracted_csv
            )
        except (OSError, UnicodeError, ValueError):
            self.full_translation_page.show_failure(
                "无法读取扫描清单，请返回扫描页面重新扫描。"
            )
            self._show_full_translation_page()
            return
        selected = tuple(
            record
            for record in self.scan_selection.selected_records(records)
            if record.original.strip() and not record.skip_status.strip()
        )
        self._full_selected_ids = frozenset(
            record.id for record in selected
        )
        translated = frozenset(
            record.id
            for record in selected
            if record.translation.strip()
        )
        self._full_pending_ids = self._full_selected_ids - translated
        self._full_result = None
        self._full_task_id = f"full-{uuid4().hex}"
        self._full_current_category = ""
        self._full_elapsed_previous = 0.0
        self.full_translation_page.show_ready(
            FullTranslationPageViewModel.ready(
                total_count=len(self._full_selected_ids),
                translated_count=len(translated),
            )
        )
        self._show_full_translation_page()
        if self._full_selected_ids and not self._full_pending_ids:
            self._full_completed(
                FullTranslationTaskResult(
                    total_count=len(self._full_selected_ids),
                    successful_count=len(self._full_selected_ids),
                    failed_ids=frozenset(),
                    remaining_ids=frozenset(),
                    usage=TranslationUsageSummary(),
                    elapsed_seconds=0.0,
                    task_id=self._full_task_id,
                )
            )

    @Slot()
    def show_trial_translation_result(self) -> None:
        if self._full_running:
            return
        self.stage = WorkflowStage.TRIAL_TRANSLATION
        self.pages.setCurrentWidget(self.trial_translation_page)
        self.home_nav_button.setChecked(False)
        self.footer_status.setText("试译完成")

    @Slot()
    def start_full_translation(self) -> None:
        self._start_full_translation(self._full_pending_ids)

    @Slot()
    def retry_failed_full_translation(self) -> None:
        if self._full_result is None or not self._full_result.failed_ids:
            return
        self._start_full_translation(self._full_result.failed_ids)

    def _start_full_translation(
        self,
        target_ids: frozenset[str],
    ) -> None:
        if self._task_running() or self._close_when_idle:
            return
        if (
            self.current_inspection is None
            or self.translation_session_config is None
            or not self._full_selected_ids
            or not target_ids
        ):
            return
        paths = project_paths(self.current_inspection.input_directory)
        try:
            current_records = read_extracted_csv(paths.extracted_csv)
        except (OSError, UnicodeError, ValueError):
            self.full_translation_page.show_failure(
                "无法读取扫描清单，请返回扫描页面重新扫描。"
            )
            return
        self._full_translated_before_run = sum(
            record.id in self._full_selected_ids
            and bool(record.translation.strip())
            for record in current_records
        )
        self._full_running = True
        self._full_started_at = perf_counter()
        self.full_translation_page.show_running(
            retry=(
                self._full_result is not None
                and target_ids == self._full_result.failed_ids
            )
        )
        self.footer_status.setText("正在进行完整翻译")
        task = FullTranslationTask(
            self.current_inspection.input_directory,
            self.translation_session_config,
            selected_ids=self._full_selected_ids,
            target_ids=target_ids,
            task_id=self._full_task_id,
            translator_factory=self._full_translator_factory,
        )
        task.signals.progress.connect(self._full_progress)
        task.signals.translation_event.connect(
            self._full_translation_event
        )
        task.signals.completed.connect(self._full_completed)
        task.signals.failed.connect(self._full_failed)
        self._full_task = task
        self._active_task = task
        self._thread_pool.start(task)

    @Slot(object)
    def _full_progress(self, progress: TranslationProgress) -> None:
        if not self._full_running:
            return
        self.full_translation_page.update_progress(
            FullTranslationPageViewModel.from_progress(
                progress,
                total_count=len(self._full_selected_ids),
                translated_before_run=self._full_translated_before_run,
                current_category=self._full_current_category,
                elapsed_seconds=(
                    self._full_elapsed_previous
                    + perf_counter()
                    - self._full_started_at
                ),
            )
        )

    @Slot(object)
    def _full_translation_event(self, event: object) -> None:
        if not self._full_running or not isinstance(
            event,
            TranslationStarted,
        ):
            return
        category_id = SOURCE_TYPE_TO_CATEGORY.get(
            event.source_type,
            ScanCategoryId.OTHER_SUPPORTED,
        )
        self._full_current_category = CATEGORY_DEFINITIONS[
            category_id
        ].title
        self.full_translation_page.show_current_category(
            self._full_current_category
        )

    @Slot(object)
    def _full_completed(
        self,
        result: FullTranslationTaskResult,
    ) -> None:
        self._full_running = False
        self._active_task = None
        self._full_task = None
        self._full_elapsed_previous += result.elapsed_seconds
        self._full_result = result
        self._full_pending_ids = result.remaining_ids
        view_model = FullTranslationPageViewModel.from_result(
            result,
            current_category=self._full_current_category,
            elapsed_seconds=self._full_elapsed_previous,
        )
        self.full_translation_page.show_result(view_model)
        self.footer_status.setText("完整翻译任务结束")
        if view_model.is_complete:
            self.show_translation_review()
        self._close_if_pending()

    @Slot(object)
    def _full_failed(self, failure: TaskFailure) -> None:
        self._full_running = False
        self._active_task = None
        self._full_task = None
        self.full_translation_page.show_failure(failure.message)
        self.footer_status.setText("完整翻译未能继续")
        self._close_if_pending()

    @Slot()
    def pause_full_translation(self) -> None:
        if self._full_task is None or not self._full_running:
            return
        self._full_task.pause()
        self.full_translation_page.show_pause_requested()
        self.footer_status.setText("将在当前批次完成后暂停")

    @Slot()
    def resume_full_translation(self) -> None:
        if self._full_task is None or not self._full_running:
            return
        self._full_task.resume()
        self.full_translation_page.show_resumed()
        self.footer_status.setText("继续完整翻译")

    @Slot()
    def show_full_translation_result(self) -> None:
        self._show_full_translation_page()

    @Slot()
    def show_translation_review(self) -> None:
        self.stage = WorkflowStage.TRANSLATION_REVIEW
        self.pages.setCurrentWidget(self.translation_review_page)
        self.home_nav_button.setChecked(False)
        self.footer_status.setText("检查译文")
        if self.current_inspection is None:
            self.translation_review_page.show_load_failure(
                "无法确定当前整合包，请返回并重新选择项目。"
            )
            return
        try:
            records, issues = load_translation_review(
                project_paths(
                    self.current_inspection.input_directory
                ).extracted_csv
            )
        except (OSError, UnicodeError, ValueError):
            self.translation_review_page.show_load_failure(
                "无法读取译文清单，原有文件没有被修改。"
            )
            return
        self.translation_review_page.show_review(
            TranslationReviewPageViewModel.from_data(records, issues)
        )

    @Slot(str, str)
    def save_review_translation(
        self,
        record_id: str,
        translation: str,
    ) -> None:
        self._apply_review_action(
            record_id,
            ReviewAction.EDIT,
            translation=translation,
            success_message="译文已保存，检查结果已刷新。",
        )

    @Slot(str)
    def approve_review_record(self, record_id: str) -> None:
        self._apply_review_action(
            record_id,
            ReviewAction.APPROVE,
            success_message="已标记为审核通过。",
        )

    @Slot(str)
    def mark_review_record_for_retranslation(
        self,
        record_id: str,
    ) -> None:
        self._apply_review_action(
            record_id,
            ReviewAction.NEEDS_RETRANSLATE,
            success_message="已标记为需要重译，本轮不会调用 API。",
        )

    @Slot(str)
    def skip_review_record(self, record_id: str) -> None:
        self._apply_review_action(
            record_id,
            ReviewAction.SKIP,
            success_message="已跳过该条目。",
        )

    def _apply_review_action(
        self,
        record_id: str,
        action: ReviewAction,
        *,
        translation: str = "",
        success_message: str,
    ) -> None:
        if self.current_inspection is None:
            return
        csv_path = project_paths(
            self.current_inspection.input_directory
        ).extracted_csv
        try:
            update_translation_review_record(
                csv_path,
                record_id,
                action,
                translation=translation,
            )
            records, issues = load_translation_review(csv_path)
        except (OSError, UnicodeError, ValueError):
            self.translation_review_page.show_feedback(
                "保存失败，原有译文清单已经保留。",
                error=True,
            )
            return
        self.translation_review_page.show_review(
            TranslationReviewPageViewModel.from_data(records, issues),
            selected_id=record_id,
        )
        self.translation_review_page.show_feedback(success_message)

    @Slot(str)
    def show_retranslate_placeholder(self, _record_id: str) -> None:
        self.translation_review_page.show_feedback(
            "重新翻译当前项将在下一批接入，本轮没有调用 API。"
        )

    @Slot()
    def show_build_install(self) -> None:
        if self._build_running:
            return
        self.stage = WorkflowStage.BUILD_INSTALL
        self.pages.setCurrentWidget(self.build_install_page)
        self.home_nav_button.setChecked(False)
        self.footer_status.setText("生成与安装")
        if self.current_inspection is None:
            self.build_install_page.show_failure(
                "无法确定当前整合包，请返回并重新选择项目。"
            )
            return
        if self._build_result is None:
            self.build_install_page.show_ready(
                BuildInstallPageViewModel.ready(
                    output_directory=str(
                        project_paths(
                            self.current_inspection.input_directory
                        ).output_dir
                    )
                )
            )
        else:
            self.build_install_page.show_result(
                BuildInstallPageViewModel.from_result(
                    self._build_result
                )
            )

    @Slot()
    def start_build(self) -> None:
        if (
            self._task_running()
            or self._close_when_idle
            or self.current_inspection is None
        ):
            return
        paths = project_paths(self.current_inspection.input_directory)
        self._build_running = True
        self.build_install_page.show_running("正在生成资源包")
        self.footer_status.setText("正在生成资源包")
        task = BuildTask(
            modpack_dir=self.current_inspection.input_directory,
            csv_path=paths.extracted_csv,
            output_dir=paths.output_dir,
            minecraft_version=self.current_inspection.minecraft_version,
            service=self._build_service,
        )
        task.signals.completed.connect(self._build_completed)
        task.signals.failed.connect(self._build_operation_failed)
        self._active_task = task
        self._thread_pool.start(task)

    @Slot(object)
    def _build_completed(self, result: BuildWorkflowResult) -> None:
        self._finish_build_operation()
        self._build_result = result
        self.build_install_page.show_result(
            BuildInstallPageViewModel.from_result(result)
        )
        self.footer_status.setText("资源包生成完成")
        self._close_if_pending()

    @Slot()
    def start_export(self) -> None:
        if not self._can_use_build_result():
            return
        self._build_running = True
        self.build_install_page.show_running("正在导出 ZIP")
        self.footer_status.setText("正在导出 ZIP")
        task = ExportTask(
            output_dir=self._build_result.output_dir,
            service=self._export_service,
        )
        task.signals.completed.connect(self._export_completed)
        task.signals.failed.connect(self._build_operation_failed)
        self._active_task = task
        self._thread_pool.start(task)

    @Slot(object)
    def _export_completed(self, result: ExportWorkflowResult) -> None:
        self._finish_build_operation()
        self._export_result = result
        self.completion_page.show_result(
            CompletionPageViewModel.exported(result)
        )
        self._show_completion_page("ZIP 导出完成")
        self._close_if_pending()

    @Slot()
    def start_install(self) -> None:
        if (
            not self._can_use_build_result()
            or self.current_inspection is None
        ):
            return
        self._build_running = True
        self.build_install_page.show_running("正在安装汉化包")
        self.footer_status.setText("正在安装汉化包")
        task = InstallTask(
            modpack_dir=self.current_inspection.input_directory,
            output_dir=self._build_result.output_dir,
            service=self._install_service,
        )
        task.signals.completed.connect(self._install_completed)
        task.signals.failed.connect(self._build_operation_failed)
        self._active_task = task
        self._thread_pool.start(task)

    @Slot(object)
    def _install_completed(self, result: InstallResult) -> None:
        self._finish_build_operation()
        self._install_result = result
        self.completion_page.show_result(
            CompletionPageViewModel.installed(result)
        )
        self._show_completion_page("汉化包安装完成")
        self._close_if_pending()

    @Slot()
    def start_install_rollback(self) -> None:
        if (
            self._task_running()
            or self._close_when_idle
            or self.current_inspection is None
            or self._install_result is None
        ):
            return
        self._build_running = True
        self.completion_page.show_running("正在撤销本次安装")
        self.footer_status.setText("正在撤销本次安装")
        task = RollbackTask(
            modpack_dir=self.current_inspection.input_directory,
            backup_dir=self._install_result.backup_dir,
            service=self._rollback_service,
        )
        task.signals.completed.connect(self._rollback_completed)
        task.signals.failed.connect(self._completion_operation_failed)
        self._active_task = task
        self._thread_pool.start(task)

    @Slot(object)
    def _rollback_completed(self, result: RollbackResult) -> None:
        self._finish_build_operation()
        self._install_result = None
        self.completion_page.show_result(
            CompletionPageViewModel.rolled_back(result)
        )
        self.footer_status.setText("本次安装已撤销")
        self._close_if_pending()

    @Slot(object)
    def _build_operation_failed(self, failure: TaskFailure) -> None:
        self._finish_build_operation()
        if self._build_result is None:
            self.build_install_page.show_failure(failure.message)
        else:
            self.build_install_page.show_result(
                BuildInstallPageViewModel.from_result(
                    self._build_result
                )
            )
            self.build_install_page.show_feedback(
                failure.message,
                error=True,
            )
        self.stage = WorkflowStage.BUILD_INSTALL
        self.pages.setCurrentWidget(self.build_install_page)
        self.footer_status.setText("生成或安装未完成")
        self._close_if_pending()

    @Slot(object)
    def _completion_operation_failed(self, failure: TaskFailure) -> None:
        self._finish_build_operation()
        self.completion_page.show_failure(failure.message)
        self.footer_status.setText("撤销安装未完成")
        self._close_if_pending()

    @Slot()
    def open_output_directory(self) -> None:
        if self.current_inspection is None:
            return
        output_dir = project_paths(
            self.current_inspection.input_directory
        ).output_dir
        if not output_dir.is_dir():
            self.build_install_page.show_feedback(
                "输出目录尚不存在，请先生成资源包。",
                error=True,
            )
            return
        if not self._directory_opener(output_dir):
            message = "无法打开输出目录，请在文件管理器中手动打开。"
            if self.stage is WorkflowStage.COMPLETION:
                self.completion_page.show_failure(message)
            else:
                self.build_install_page.show_feedback(
                    message,
                    error=True,
                )

    def _show_completion_page(self, status: str) -> None:
        self.stage = WorkflowStage.COMPLETION
        self.pages.setCurrentWidget(self.completion_page)
        self.home_nav_button.setChecked(False)
        self.footer_status.setText(status)

    def _can_use_build_result(self) -> bool:
        return (
            not self._task_running()
            and not self._close_when_idle
            and self._build_result is not None
            and not self._build_result.errors
            and self._build_result.installable_files > 0
        )

    def _finish_build_operation(self) -> None:
        self._build_running = False
        self._active_task = None

    @staticmethod
    def _open_directory(path: Path) -> bool:
        return QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(path))
        )

    def _show_full_translation_page(self) -> None:
        self.stage = WorkflowStage.FULL_TRANSLATION
        self.pages.setCurrentWidget(self.full_translation_page)
        self.home_nav_button.setChecked(False)
        self.footer_status.setText("等待完整翻译")

    def _choose_directory(self) -> str | None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "选择 Minecraft 整合包目录",
            "",
            QFileDialog.Option.ShowDirsOnly,
        )
        return selected or None

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._task_running():
            if self._full_task is not None:
                self._full_task.request_stop()
            event.ignore()
            if not self._close_when_idle:
                self._close_when_idle = True
                self._disable_task_starters_for_pending_close()
                self.footer_status.setText(
                    self._pending_close_message()
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
        self.translation_config_page.back_button.setEnabled(False)
        self.translation_config_page.validate_button.setEnabled(False)
        self.translation_config_page.continue_button.setEnabled(False)
        self.trial_translation_page.back_button.setEnabled(False)
        self.trial_translation_page.start_button.setEnabled(False)
        self.trial_translation_page.retry_button.setEnabled(False)
        self.trial_translation_page.continue_button.setEnabled(False)
        self.full_translation_page.back_button.setEnabled(False)
        self.full_translation_page.start_button.setEnabled(False)
        self.full_translation_page.pause_button.setEnabled(False)
        self.full_translation_page.resume_button.setEnabled(False)
        self.full_translation_page.retry_button.setEnabled(False)
        self.build_install_page.back_button.setEnabled(False)
        self.build_install_page.build_button.setEnabled(False)
        self.build_install_page.open_button.setEnabled(False)
        self.build_install_page.export_button.setEnabled(False)
        self.build_install_page.install_button.setEnabled(False)
        self.completion_page.back_button.setEnabled(False)
        self.completion_page.open_button.setEnabled(False)
        self.completion_page.rollback_button.setEnabled(False)

    def _close_if_pending(self) -> None:
        if not self._close_when_idle or self._close_scheduled:
            return
        self._close_scheduled = True
        self._disable_task_starters_for_pending_close()
        self.footer_status.setText(
            self._pending_close_message()
        )
        QTimer.singleShot(0, self.close)

    def _task_running(self) -> bool:
        return (
            self._inspection_running
            or self._scan_running
            or self._trial_running
            or self._full_running
            or self._build_running
        )

    def _pending_close_message(self) -> str:
        if self._trial_running:
            return "正在安全结束当前试译，完成后将自动关闭"
        if self._full_running:
            return "正在保存当前翻译批次，完成后将自动关闭"
        if self._build_running:
            return "正在完成当前文件操作，完成后将自动关闭"
        return "正在安全结束当前扫描，完成后将自动关闭"

    def _clear_full_translation_state(self) -> None:
        self._full_task = None
        self._full_selected_ids = frozenset()
        self._full_pending_ids = frozenset()
        self._full_result = None
        self._full_task_id = ""
        self._full_current_category = ""
        self._full_translated_before_run = 0
        self._full_elapsed_previous = 0.0
        self._full_started_at = 0.0

    def _clear_build_state(self) -> None:
        self._build_running = False
        self._build_result = None
        self._install_result = None
        self._export_result = None


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
            pages = (
                window.home_page,
                window.inspection_page,
                window.scan_page,
                window.translation_config_page,
                window.trial_translation_page,
                window.full_translation_page,
                window.translation_review_page,
                window.build_install_page,
                window.completion_page,
            )
            pages_switchable = True
            for page in pages:
                window.pages.setCurrentWidget(page)
                application.processEvents()
                if window.pages.currentWidget() is not page:
                    pages_switchable = False
                    break
            window.pages.setCurrentWidget(window.home_page)
            application.processEvents()
            smoke_state["completed"] = True
            smoke_state["valid"] = (
                window.isVisible()
                and window.home_page.select_button.isEnabled()
                and window.pages.currentWidget() is window.home_page
                and pages_switchable
                and get_version() != UNKNOWN_VERSION
            )
            window.close()
            application.quit()

        QTimer.singleShot(250, complete_smoke_test)

    exit_code = application.exec()
    if smoke_test and (not smoke_state["completed"] or not smoke_state["valid"]):
        return 1
    return exit_code
