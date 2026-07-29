from __future__ import annotations

import sys
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import replace
from decimal import Decimal, InvalidOperation
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
from mc_han.qt.pages.settings_page import SettingsPage
from mc_han.qt.pages.translation_config_page import TranslationConfigPage
from mc_han.qt.pages.translation_plan_page import TranslationPlanPage
from mc_han.qt.pages.trial_translation_page import TrialTranslationPage
from mc_han.qt.pages.translation_review_page import TranslationReviewPage
from mc_han.qt.project_session import (
    CloseDecision,
    ProjectSession,
    TaskKind,
    WorkflowController,
)
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
from mc_han.qt.theme import (
    ThemeManager,
    ThemePreference,
)
from mc_han.qt.translation_config_view_models import (
    TranslationConfigPageViewModel,
    TranslationProvider,
    TranslationSessionConfig,
    recommended_translation_config,
    validate_translation_config,
    with_recommended_values,
    create_translator,
)
from mc_han.qt.translation_plan_view_models import (
    TranslationPlanPageViewModel,
)
from mc_han.qt.trial_view_models import TrialPageViewModel
from mc_han.qt.translation_review_view_models import (
    TranslationReviewPageViewModel,
)
from mc_han.qt.view_models import InspectionPageViewModel, WorkflowStage
from mc_han.release_info import about_text
from mc_han.services.modpack_inspector import inspect_modpack
from mc_han.services.credentials import CredentialStore, MemoryCredentialStore
from mc_han.services.project_discovery import (
    DiscoveredProject,
    discover_modpacks,
)
from mc_han.services.recent_projects import (
    MemoryRecentProjectsStore,
    RecentProject,
    RecentProjectsStore,
)
from mc_han.services.build_install import (
    BuildWorkflowResult,
    ExportWorkflowResult,
    build_localization_package,
    export_localization_zip,
    install_localization_package,
    rollback_localization_install,
)
from mc_han.services.scan_service import scan_and_classify
from mc_han.services.translation_planning import (
    build_translation_plan_comparison,
)
from mc_han.services.translation_rules import TranslationRuleStore
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
from mc_han.settings import (
    UserSettings,
    config_path,
    load_settings,
    save_settings,
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
from mc_han.translator.engine import (
    TranslationBatchCompleted,
    TranslationItemCompleted,
    TranslationItemFailed,
    TranslationProgress,
    TranslationStarted,
)
from mc_han.usage.ledger import UsageLedger
from mc_han.usage.service import UsageQueryService
from mc_han.usage.models import TranslationUsageSummary
from mc_han.workflow.trial_models import (
    TrialProgressEvent,
    TrialSampleResult,
    TrialTranslationResult,
    TrialSampleStatus,
)
from mc_han.workflow.translation_plan import (
    TranslationPlan,
    TranslationPlanComparison,
    TranslationPlanMode,
)
from mc_han.workflow.translation_rules import (
    TranslationRuleScope,
    TranslationRuleType,
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
CloseDecisionProvider = Callable[[str, bool], CloseDecision]
ProjectDiscoveryService = Callable[
    [Sequence[Path]],
    tuple[DiscoveredProject, ...],
]
TranslationPlanningService = Callable[..., TranslationPlanComparison]

WORKFLOW_STEP_LABELS = (
    "整合包",
    "汉化",
    "安装",
)

STAGE_STEP_INDEX = {
    WorkflowStage.WELCOME: 0,
    WorkflowStage.INSPECTING: 0,
    WorkflowStage.INSPECTION_RESULT: 0,
    WorkflowStage.SCANNING: 0,
    WorkflowStage.SCAN_RESULT: 0,
    WorkflowStage.TRANSLATION_PLAN: 1,
    WorkflowStage.TRANSLATION_CONFIG: 1,
    WorkflowStage.TRIAL_TRANSLATION: 1,
    WorkflowStage.FULL_TRANSLATION: 1,
    WorkflowStage.TRANSLATION_CHECK_PLACEHOLDER: 1,
    WorkflowStage.TRANSLATION_REVIEW: 1,
    WorkflowStage.BUILD_PLACEHOLDER: 2,
    WorkflowStage.BUILD_INSTALL: 2,
    WorkflowStage.COMPLETION: 2,
}


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
        close_decision_provider: CloseDecisionProvider | None = None,
        recent_projects_store: RecentProjectsStore | None = None,
        project_discovery_service: ProjectDiscoveryService | None = None,
        credential_store: CredentialStore | None = None,
        settings_path: Path | None = None,
        translation_planning_service: TranslationPlanningService = (
            build_translation_plan_comparison
        ),
        theme_manager: ThemeManager | None = None,
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
        self._close_decision_provider = (
            close_decision_provider or self._prompt_close_decision
        )
        self._recent_projects_store = (
            recent_projects_store or MemoryRecentProjectsStore()
        )
        self._project_discovery_service = (
            project_discovery_service
            or (lambda _manual_paths: ())
        )
        self._credential_store = credential_store or MemoryCredentialStore()
        self._settings_path = Path(settings_path) if settings_path else None
        self._translation_planning_service = translation_planning_service
        self._saved_settings = (
            load_settings(self._settings_path)
            if self._settings_path is not None
            else UserSettings()
        )
        application = QApplication.instance()
        if application is None:
            raise RuntimeError("QApplication must exist before MainWindow")
        self._owns_theme_manager = theme_manager is None
        self.theme_manager = theme_manager or ThemeManager(
            application,
            preference=_saved_theme_preference(self._saved_settings),
        )
        self._recent_projects = self._recent_projects_store.load()
        self._discovered_projects: tuple[DiscoveredProject, ...] = ()
        self._last_directory = self._recent_projects.last_directory
        self._thread_pool = QThreadPool(self)
        self._thread_pool.setMaxThreadCount(1)
        self.workflow_controller = WorkflowController(self)
        self.session: ProjectSession = self.workflow_controller.session
        self.workflow_controller.changed.connect(self._session_changed)
        self._project_save_timer = QTimer(self)
        self._project_save_timer.setSingleShot(True)
        self._project_save_timer.setInterval(350)
        self._project_save_timer.timeout.connect(
            self._persist_project_progress
        )
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
        self.translation_plan_comparison: TranslationPlanComparison | None = None
        self.selected_translation_plan: TranslationPlan | None = None
        self._translation_plan_mode = _saved_plan_mode(self._saved_settings)
        self._budget_limit_usd = _saved_budget(self._saved_settings)
        self._config_returns_to_plan = False
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
        self._full_usage = TranslationUsageSummary()
        self._full_activity: list[str] = []
        self._full_budget_warning_emitted = False
        self._full_last_progress: TranslationProgress | None = None
        self._build_result: BuildWorkflowResult | None = None
        self._install_result: InstallResult | None = None
        self._export_result: ExportWorkflowResult | None = None
        self._review_unlocked = False
        self._build_unlocked = False
        self._notice_boxes: list[QMessageBox] = []
        self.stage = WorkflowStage.WELCOME

        self.setWindowTitle("mc-han")
        self.resize(1120, 720)
        self.setMinimumSize(920, 620)

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
        self.translation_plan_page = TranslationPlanPage()
        self.translation_config_page = TranslationConfigPage()
        self.trial_translation_page = TrialTranslationPage()
        self.full_translation_page = FullTranslationPage()
        self.translation_review_page = TranslationReviewPage()
        self.build_install_page = BuildInstallPage()
        self.completion_page = CompletionPage()
        self.settings_page = SettingsPage()
        self.pages.addWidget(self.home_page)
        self.pages.addWidget(self.inspection_page)
        self.pages.addWidget(self.scan_page)
        self.pages.addWidget(self.translation_plan_page)
        self.pages.addWidget(self.translation_config_page)
        self.pages.addWidget(self.trial_translation_page)
        self.pages.addWidget(self.full_translation_page)
        self.pages.addWidget(self.translation_review_page)
        self.pages.addWidget(self.build_install_page)
        self.pages.addWidget(self.completion_page)
        self.pages.addWidget(self.settings_page)
        self._page_keys = {
            self.home_page: "home",
            self.inspection_page: "inspection",
            self.scan_page: "scan",
            self.translation_plan_page: "translation_plan",
            self.translation_config_page: "translation_config",
            self.trial_translation_page: "trial_translation",
            self.full_translation_page: "full_translation",
            self.translation_review_page: "translation_review",
            self.build_install_page: "build_install",
            self.completion_page: "completion",
            self.settings_page: "settings",
        }
        self._pages_by_key = {
            page_key: page for page, page_key in self._page_keys.items()
        }
        self.pages.currentChanged.connect(self._current_page_changed)
        root_layout.addWidget(self.pages, stretch=1)
        root_layout.addWidget(self._build_footer())
        self.setCentralWidget(root)
        self.theme_manager.apply(self)

        self.home_page.select_requested.connect(self.choose_and_inspect)
        self.home_page.project_requested.connect(
            lambda path: self.start_inspection(Path(path))
        )
        self.inspection_page.reselect_requested.connect(self.choose_and_inspect)
        self.inspection_page.home_requested.connect(self.show_home)
        self.inspection_page.scan_requested.connect(self.start_scan)
        self.scan_page.back_requested.connect(self.show_inspection_result)
        self.scan_page.rescan_requested.connect(self.start_scan)
        self.scan_page.continue_requested.connect(self.show_translation_plan)
        self.scan_page.select_all_requested.connect(self.select_all_scan_categories)
        self.scan_page.clear_selection_requested.connect(
            self.clear_scan_categories
        )
        self.scan_page.restore_defaults_requested.connect(
            self.restore_scan_category_defaults
        )
        self.scan_page.category_toggled.connect(self.set_scan_category_selected)
        self.translation_plan_page.back_requested.connect(
            self.show_scan_result
        )
        self.translation_plan_page.advanced_requested.connect(
            self.show_translation_config_from_plan
        )
        self.translation_plan_page.continue_requested.connect(
            self.confirm_translation_plan
        )
        self.translation_plan_page.mode_changed.connect(
            self.change_translation_plan_mode
        )
        self.translation_plan_page.budget_changed.connect(
            self.change_translation_budget
        )
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
            self.show_translation_plan
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
        self.trial_translation_page.satisfied_requested.connect(
            self.mark_trial_sample_satisfied
        )
        self.trial_translation_page.feedback_requested.connect(
            self.save_trial_feedback
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
            self.mark_review_record_for_retranslation
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
        self.settings_page.back_requested.connect(self.close_settings)
        self.settings_page.delete_credentials_requested.connect(
            self.delete_saved_credentials
        )
        self.settings_page.theme_changed.connect(
            self.change_theme_preference
        )
        self._refresh_home_projects()
        self.show_home()

    def _build_top_bar(self) -> QFrame:
        top_bar = QFrame()
        top_bar.setObjectName("TopBar")
        top_bar.setFixedHeight(108)
        layout = QVBoxLayout(top_bar)
        layout.setContentsMargins(28, 8, 28, 8)
        layout.setSpacing(4)

        brand_row = QHBoxLayout()
        brand_row.setSpacing(8)
        brand = QLabel("mc-han")
        brand.setObjectName("BrandLabel")
        brand_row.addWidget(brand)
        self.activity_label = QLabel("当前无活动任务")
        self.activity_label.setObjectName("MutedLabel")
        brand_row.addWidget(self.activity_label)
        self.activity_progress_label = QLabel("")
        self.activity_progress_label.setObjectName("MutedLabel")
        brand_row.addWidget(self.activity_progress_label)
        brand_row.addStretch()
        self.project_label = QLabel("未选择项目")
        self.project_label.setObjectName("MutedLabel")
        brand_row.addWidget(self.project_label)
        self.settings_button = QPushButton("设置")
        self.settings_button.setToolTip("查看应用设置与隐私说明")
        self.settings_button.clicked.connect(self.show_settings)
        brand_row.addWidget(self.settings_button)
        about_button = QPushButton("关于")
        about_button.setToolTip("查看版本和开源信息")
        about_button.clicked.connect(self.show_about)
        brand_row.addWidget(about_button)
        layout.addLayout(brand_row)

        step_row = QHBoxLayout()
        step_row.setSpacing(4)
        self.workflow_step_buttons: list[QPushButton] = []
        for index, label in enumerate(WORKFLOW_STEP_LABELS):
            button = self._nav_button(f"{index + 1}. {label}")
            button.clicked.connect(
                lambda _checked=False, step=index: self._navigate_to_step(step)
            )
            self.workflow_step_buttons.append(button)
            step_row.addWidget(button, stretch=1)
        self.home_nav_button = self.workflow_step_buttons[0]
        layout.addLayout(step_row)
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
    def _nav_button(label: str) -> QPushButton:
        button = QPushButton(label)
        button.setProperty("nav", True)
        button.setCheckable(True)
        return button

    @property
    def stage(self) -> WorkflowStage:
        return self.session.stage

    @stage.setter
    def stage(self, value: WorkflowStage) -> None:
        self.workflow_controller.set_stage(value)
        if hasattr(self, "workflow_step_buttons"):
            self._refresh_step_navigation()

    @Slot(object)
    def _session_changed(self, _session: ProjectSession) -> None:
        if not hasattr(self, "project_label"):
            return
        self.project_label.setText(self.session.project_name)
        task = self.session.active_task
        if task is None:
            self.activity_label.setText("当前无活动任务")
            self.activity_progress_label.setText("")
        else:
            self.activity_label.setText(f"活动任务：{task.label}")
            self.activity_progress_label.setText(task.progress)
        self._refresh_step_navigation()
        self._refresh_disabled_reasons()
        if self.current_inspection is not None:
            self._project_save_timer.start()

    @Slot(int)
    def _current_page_changed(self, index: int) -> None:
        page = self.pages.widget(index)
        page_key = self._page_keys.get(page)
        if page_key and self.session.current_page != page_key:
            self.workflow_controller.set_page(page_key)

    def _show_page(self, page: QWidget, *, force: bool = False) -> None:
        page_key = self._page_keys[page]
        if (
            not force
            and self.pages.currentWidget() is self.settings_page
            and page is not self.settings_page
        ):
            self.workflow_controller.update_settings_return_page(page_key)
            return
        self.pages.setCurrentWidget(page)
        if self.session.current_page != page_key:
            self.workflow_controller.set_page(page_key)

    def _refresh_step_navigation(self) -> None:
        current_step = STAGE_STEP_INDEX[self.stage]
        for index, button in enumerate(self.workflow_step_buttons):
            enabled, reason = self._step_access(index)
            if self._task_running() and index != current_step:
                enabled = False
                reason = "当前任务完成后即可切换步骤"
            button.setEnabled(enabled)
            button.setChecked(index == current_step)
            button.setToolTip("" if enabled else reason)
            button.setProperty(
                "completed",
                self._step_completed(index),
            )
            button.style().unpolish(button)
            button.style().polish(button)

    def _step_access(self, index: int) -> tuple[bool, str]:
        if index == 0:
            return True, ""
        if index == 1:
            return (
                bool(
                    self.scan_selection is not None
                    and self.scan_selection.selected_record_count > 0
                ),
                "请先完成整合包扫描并选择需要汉化的内容",
            )
        return self._build_unlocked, "请先完成汉化并进入译文检查"

    def _step_completed(self, index: int) -> bool:
        completed = (
            self.current_scan_result is not None,
            self._review_unlocked,
            self._install_result is not None,
        )
        return completed[index]

    @Slot()
    def _navigate_to_step(self, index: int) -> None:
        enabled, reason = self._step_access(index)
        if self._task_running():
            self._show_action_blocked(self._active_task_reason())
            return
        if not enabled:
            self._show_action_blocked(reason)
            return
        if index == 0:
            if (
                self.current_inspection is not None
                and self.current_scan_result is None
                and self.current_inspection.can_continue
            ):
                self.start_scan()
            elif self.current_scan_result is not None:
                self.show_scan_result()
            elif self.current_inspection is not None:
                self.show_inspection_result()
            else:
                self.show_home()
            return
        if index == 1:
            if self._review_unlocked:
                self.show_translation_review()
            elif self._full_selected_ids:
                self.show_full_translation_result()
            elif self.translation_session_config is not None:
                self.show_trial_translation_result()
            else:
                self.show_translation_plan()
            return
        if self._install_result is not None:
            self._show_page(self.completion_page)
            self.stage = WorkflowStage.COMPLETION
        else:
            self.show_build_install()

    @Slot()
    def show_settings(self) -> None:
        self.theme_manager.refresh_system_theme()
        self.settings_page.set_theme(
            self.theme_manager.preference.value,
            self.theme_manager.effective_theme.value,
        )
        self._refresh_credential_status()
        self.workflow_controller.open_settings()
        self._show_page(self.settings_page, force=True)
        self.footer_status.setText("设置")

    @Slot()
    def close_settings(self) -> None:
        page_key = self.workflow_controller.close_settings()
        page = self._pages_by_key.get(page_key, self.home_page)
        self._show_page(page, force=True)
        self._refresh_step_navigation()
        self.footer_status.setText(self._session_status_text())

    @Slot(object)
    def change_theme_preference(self, value: object) -> None:
        try:
            preference = ThemePreference(str(value))
        except ValueError:
            self._show_action_blocked("无法识别所选主题。")
            return
        self.theme_manager.set_preference(preference)
        self._saved_settings = replace(
            self._saved_settings,
            theme_mode=preference.value,
        )
        self.settings_page.set_theme(
            preference.value,
            self.theme_manager.effective_theme.value,
        )
        if self._settings_path is not None:
            try:
                save_settings(self._saved_settings, path=self._settings_path)
            except OSError:
                self.settings_page.theme_status.setText(
                    "主题已切换，但未能保存到本机设置。"
                )
                return
        self.footer_status.setText("主题已更新")

    def _refresh_disabled_reasons(self) -> None:
        if not hasattr(self, "pages"):
            return
        active_reason = self._active_task_reason() if self._task_running() else ""
        for button in self.findChildren(QPushButton):
            automatic = bool(button.property("automaticDisabledReason"))
            if button.isEnabled():
                if automatic:
                    button.setToolTip("")
                    button.setProperty("automaticDisabledReason", False)
                continue
            if button.toolTip() and not automatic:
                continue
            button.setToolTip(
                active_reason or "请先完成当前步骤所需条件"
            )
            button.setProperty("automaticDisabledReason", True)

    def _show_action_blocked(self, reason: str) -> None:
        message = reason or "当前操作暂不可用，请先完成必要步骤。"
        box = QMessageBox(
            QMessageBox.Icon.Information,
            "暂时无法执行",
            message,
            QMessageBox.StandardButton.Ok,
            self,
        )
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._notice_boxes.append(box)

        def remove_box(_result: int) -> None:
            if box in self._notice_boxes:
                self._notice_boxes.remove(box)

        box.finished.connect(remove_box)
        box.open()

    def _active_task_reason(self) -> str:
        task = self.session.active_task
        if task is None:
            return ""
        return f"{task.label}正在运行，请等待当前任务结束。"

    def _session_status_text(self) -> str:
        task = self.session.active_task
        if task is not None:
            return task.label
        status_by_stage = {
            WorkflowStage.WELCOME: "准备就绪",
            WorkflowStage.INSPECTING: "正在检测整合包",
            WorkflowStage.INSPECTION_RESULT: "检测完成",
            WorkflowStage.SCANNING: "正在扫描整合包",
            WorkflowStage.SCAN_RESULT: "扫描与分类完成",
            WorkflowStage.TRANSLATION_CONFIG: "配置翻译服务",
            WorkflowStage.TRIAL_TRANSLATION: "试译",
            WorkflowStage.FULL_TRANSLATION: "完整翻译",
            WorkflowStage.TRANSLATION_REVIEW: "检查译文",
            WorkflowStage.BUILD_INSTALL: "生成与安装",
            WorkflowStage.COMPLETION: "操作完成",
        }
        return status_by_stage.get(self.stage, "准备就绪")

    def _begin_task(
        self,
        task: object,
        kind: TaskKind,
        label: str,
        *,
        cancel: Callable[[], None] | None = None,
        pause: Callable[[], None] | None = None,
        resume: Callable[[], None] | None = None,
    ) -> bool:
        started, reason = self.workflow_controller.begin_task(
            kind,
            label,
            task,
            cancel=cancel,
            pause=pause,
            resume=resume,
        )
        if not started:
            self._show_action_blocked(reason)
        else:
            QTimer.singleShot(0, self._refresh_disabled_reasons)
        return started

    def _finish_task(self, task: object | None = None) -> None:
        self.workflow_controller.finish_task(task)
        QTimer.singleShot(0, self._refresh_disabled_reasons)

    @Slot()
    def choose_and_inspect(self) -> None:
        if self._task_running() or self._close_when_idle:
            self._show_action_blocked(
                self._active_task_reason()
                or "软件正在安全关闭，暂时不能选择新项目。"
            )
            return
        selected = self._directory_picker()
        if selected:
            self.start_inspection(Path(selected))

    def start_inspection(self, path: Path) -> None:
        if self._task_running() or self._close_when_idle:
            self._show_action_blocked(
                self._active_task_reason()
                or "软件正在安全关闭，暂时不能检测新项目。"
            )
            return
        task = InspectionTask(path, self._inspection_service)
        if not self._begin_task(
            task,
            TaskKind.INSPECTION,
            "检测整合包",
        ):
            return
        self.workflow_controller.set_project(
            path,
            path.name or "正在检测的项目",
        )
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
        self._review_unlocked = False
        self._build_unlocked = False
        self.stage = WorkflowStage.INSPECTING
        self.home_page.select_button.setEnabled(False)
        self.inspection_page.show_loading()
        self._show_page(self.inspection_page)
        self.footer_status.setText("正在检测整合包")

        task.signals.completed.connect(self._inspection_completed)
        task.signals.failed.connect(self._inspection_failed)
        self._thread_pool.start(task)

    @Slot(object)
    def _inspection_completed(self, inspection: ModpackInspection) -> None:
        self._inspection_running = False
        self._finish_task()
        self.current_inspection = inspection
        self.workflow_controller.set_project(
            inspection.input_directory,
            inspection.display_name,
        )
        self.stage = WorkflowStage.INSPECTION_RESULT
        self.home_page.select_button.setEnabled(True)
        self.project_label.setText(inspection.display_name or "未选择项目")
        self.footer_status.setText("检测完成")
        self.inspection_page.show_result(
            InspectionPageViewModel.from_inspection(inspection)
        )
        self._remember_inspection(inspection)
        self._close_if_pending()

    @Slot(object)
    def _inspection_failed(self, failure: TaskFailure) -> None:
        self._inspection_running = False
        self._finish_task()
        self.stage = WorkflowStage.INSPECTION_RESULT
        self.home_page.select_button.setEnabled(True)
        self.footer_status.setText("检测未完成")
        self.inspection_page.show_failure(failure)
        self._close_if_pending()

    @Slot()
    def start_scan(self) -> None:
        if self._task_running() or self._close_when_idle:
            self._show_action_blocked(
                self._active_task_reason()
                or "软件正在安全关闭，暂时不能开始扫描。"
            )
            return
        inspection = self.current_inspection
        if inspection is None or not inspection.can_continue:
            self._show_action_blocked("请先选择并识别有效的整合包。")
            return
        task = ScanTask(
            inspection.input_directory,
            self._scan_service,
            inspection.existing_chinese,
        )
        if not self._begin_task(task, TaskKind.SCAN, "扫描整合包"):
            return
        self._scan_running = True
        self.current_scan_result = None
        self.scan_selection = None
        self.stage = WorkflowStage.SCANNING
        self.scan_page.show_loading(inspection.display_name)
        self._show_page(self.scan_page)
        self.footer_status.setText("正在扫描整合包")

        task.signals.progress.connect(self._scan_progress)
        task.signals.completed.connect(self._scan_completed)
        task.signals.failed.connect(self._scan_failed)
        self._thread_pool.start(task)

    @Slot(object)
    def _scan_progress(self, event: ScanProgressEvent) -> None:
        if not self._scan_running:
            return
        progress_text = f"已发现 {event.discovered_records:,} 条"
        if event.total_jars:
            progress_text = (
                f"JAR {event.processed_jars:,}/{event.total_jars:,} · "
                f"{progress_text}"
            )
        self.workflow_controller.update_task_progress(progress_text)
        self.scan_page.update_progress(ScanProgressViewModel.from_event(event))

    @Slot(object)
    def _scan_completed(self, result: ScanClassificationResult) -> None:
        self._scan_running = False
        self._finish_task()
        self.current_scan_result = result
        self.scan_selection = ScanSelectionState.from_result(result)
        self.stage = WorkflowStage.SCAN_RESULT
        self.footer_status.setText("扫描与分类完成")
        self._render_scan_result()
        self._close_if_pending()

    @Slot(object)
    def _scan_failed(self, failure: TaskFailure) -> None:
        self._scan_running = False
        self._finish_task()
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
        self._show_page(self.scan_page)
        self._refresh_step_navigation()

    @Slot()
    def show_home(self) -> None:
        if self._task_running():
            self._show_action_blocked(self._active_task_reason())
            return
        self.stage = WorkflowStage.WELCOME
        self._refresh_home_projects()
        self._show_page(self.home_page)
        self.footer_status.setText("准备就绪")

    @Slot()
    def show_inspection_result(self) -> None:
        if self._scan_running:
            self._show_action_blocked(self._active_task_reason())
            return
        if self.current_inspection is None:
            self.show_home()
            return
        self.stage = WorkflowStage.INSPECTION_RESULT
        self._show_page(self.inspection_page)

    @Slot()
    def show_scan_result(self) -> None:
        if self._scan_running:
            self._show_action_blocked(self._active_task_reason())
            return
        if self.scan_selection is None:
            self.show_inspection_result()
            return
        self.stage = WorkflowStage.SCAN_RESULT
        self._render_scan_result()

    @Slot()
    def show_translation_plan(self) -> None:
        if (
            self.scan_selection is None
            or self.scan_selection.selected_record_count == 0
            or self.current_inspection is None
        ):
            self._show_action_blocked(
                "请先扫描并选择至少一个需要翻译的类别。"
            )
            return
        paths = project_paths(self.current_inspection.input_directory)
        config = (
            self.translation_config_draft
            or self.translation_session_config
            or self._saved_translation_config()
        )
        try:
            records = read_extracted_csv(paths.extracted_csv)
            comparison = self._translation_planning_service(
                records,
                self.scan_selection,
                provider=config.provider.value,
                base_model=config.model,
                high_quality_model=config.high_quality_model,
                concurrency=config.concurrency,
                sqlite_cache_path=paths.translations_sqlite,
                jsonl_cache_path=paths.translation_cache_jsonl,
                provenance_path=paths.provenance_sqlite,
            )
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            self.translation_plan_comparison = None
            self.selected_translation_plan = None
            self.translation_plan_page.show_failure(
                "扫描清单或翻译缓存暂时无法读取。"
                f"请返回扫描页面重试（{type(error).__name__}）。"
            )
        else:
            self.translation_plan_comparison = comparison
            self.selected_translation_plan = comparison.for_mode(
                self._translation_plan_mode
            )
            self.translation_plan_page.set_budget(self._budget_limit_usd)
            self._render_translation_plan()
        self._config_returns_to_plan = False
        self.stage = WorkflowStage.TRANSLATION_PLAN
        self._show_page(self.translation_plan_page)
        self.footer_status.setText("比较翻译方案")

    @Slot()
    def show_translation_config_from_plan(self) -> None:
        self._config_returns_to_plan = True
        self.show_translation_config()

    @Slot(object)
    def change_translation_plan_mode(
        self,
        mode: TranslationPlanMode,
    ) -> None:
        if (
            not isinstance(mode, TranslationPlanMode)
            or self.translation_plan_comparison is None
        ):
            return
        self._translation_plan_mode = mode
        self.selected_translation_plan = (
            self.translation_plan_comparison.for_mode(mode)
        )
        self._save_plan_preferences()
        self._render_translation_plan()

    @Slot(object)
    def change_translation_budget(self, budget: Decimal | None) -> None:
        if budget is not None and (
            not isinstance(budget, Decimal) or budget <= 0
        ):
            return
        self._budget_limit_usd = budget
        self._save_plan_preferences()
        self._render_translation_plan()

    def _render_translation_plan(self) -> None:
        if self.selected_translation_plan is None:
            return
        self.translation_plan_page.show_plan(
            TranslationPlanPageViewModel.from_plan(
                self.selected_translation_plan,
                self._budget_limit_usd,
            )
        )

    @Slot()
    def confirm_translation_plan(self) -> None:
        if self.selected_translation_plan is None:
            self._show_action_blocked("请先生成并选择一个翻译方案。")
            return
        if self.selected_translation_plan.exceeds_budget(
            self._budget_limit_usd
        ):
            self._show_action_blocked("预计费用超过预算，请先调整方案或预算。")
            return
        config = (
            self.translation_session_config
            or self.translation_config_draft
            or self._saved_translation_config()
        )
        if not validate_translation_config(config).valid:
            self._config_returns_to_plan = True
            self.show_translation_config()
            self.translation_config_page.show_validation(
                validate_translation_config(config)
            )
            return
        self.translation_session_config = config
        self._prepare_trial_and_show()

    @Slot()
    def show_translation_config(self) -> None:
        if self.scan_selection is None or self.scan_selection.selected_record_count == 0:
            self._show_action_blocked(
                "请先扫描并选择至少一个需要翻译的类别。"
            )
            return
        config = (
            self.translation_config_draft
            or self.translation_session_config
            or self._saved_translation_config()
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
        persisted = self._credential_store.contains_persisted(
            config.provider.value,
            config.base_url,
        )
        self.translation_config_page.save_api_key_checkbox.setChecked(
            persisted
        )
        self.translation_config_page.show_credential_status(
            (
                "已从 Windows 安全凭据存储加载 API Key。"
                if persisted
                else "不勾选时，API Key 仅保留在当前程序会话。"
            )
        )
        self.stage = WorkflowStage.TRANSLATION_CONFIG
        self._show_page(self.translation_config_page)
        self.footer_status.setText("配置翻译服务")

    @Slot()
    def return_to_scan_from_translation_config(self) -> None:
        self.translation_config_draft = (
            self.translation_config_page.current_config()
        )
        if self._config_returns_to_plan:
            self.show_translation_plan()
        else:
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
        self._save_translation_preferences(config)
        if self._config_returns_to_plan:
            self.show_translation_plan()
            return
        self._prepare_trial_and_show()

    def _prepare_trial_and_show(self) -> None:
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
        self._show_page(self.trial_translation_page)
        self.footer_status.setText("等待确认试译")

    def _saved_translation_config(self) -> TranslationSessionConfig:
        settings = self._saved_settings
        try:
            provider = TranslationProvider(
                settings.provider or TranslationProvider.DEEPSEEK.value
            )
        except ValueError:
            provider = TranslationProvider.DEEPSEEK
        recommended = recommended_translation_config(provider)
        base_url = settings.base_url or recommended.base_url
        api_key = self._credential_store.load(provider.value, base_url) or ""
        return replace(
            recommended,
            base_url=base_url,
            model=settings.model or recommended.model,
            high_quality_model=(
                settings.high_quality_model
                or recommended.high_quality_model
            ),
            api_key=api_key,
            concurrency=settings.worker_count or recommended.concurrency,
            batch_size=settings.max_batch_items or recommended.batch_size,
            timeout_seconds=(
                settings.timeout_seconds or recommended.timeout_seconds
            ),
        )

    def _save_translation_preferences(
        self,
        config: TranslationSessionConfig,
    ) -> None:
        credential_message = "API Key 仅保留在当前程序会话。"
        if self.translation_config_page.save_api_key_checkbox.isChecked():
            result = self._credential_store.save(
                config.provider.value,
                config.base_url,
                config.api_key,
            )
            credential_message = result.message
        self.translation_config_page.show_credential_status(
            credential_message
        )
        self._saved_settings = replace(
            self._saved_settings,
            provider=config.provider.value,
            model=config.model,
            high_quality_model=config.high_quality_model,
            base_url=config.base_url,
            worker_count=config.concurrency,
            max_batch_items=config.batch_size,
            timeout_seconds=config.timeout_seconds,
            plan_mode=self._translation_plan_mode.value,
            budget_limit_usd=(
                str(self._budget_limit_usd)
                if self._budget_limit_usd is not None
                else None
            ),
        )
        if self._settings_path is None:
            return
        try:
            save_settings(
                self._saved_settings,
                path=self._settings_path,
            )
        except OSError:
            self.translation_config_page.show_credential_status(
                f"{credential_message} 非敏感设置未能保存，但本次会话可继续。"
            )

    def _save_plan_preferences(self) -> None:
        self._saved_settings = replace(
            self._saved_settings,
            plan_mode=self._translation_plan_mode.value,
            budget_limit_usd=(
                str(self._budget_limit_usd)
                if self._budget_limit_usd is not None
                else None
            ),
        )
        if self._settings_path is None:
            return
        try:
            save_settings(self._saved_settings, path=self._settings_path)
        except OSError:
            self.footer_status.setText(
                "方案已应用于本次会话，但预算设置未能保存"
            )

    def _refresh_credential_status(self) -> None:
        descriptors = self._credential_store.descriptors()
        if descriptors:
            self.settings_page.show_credential_status(
                f"已安全保存 {len(descriptors)} 个翻译服务的 API Key。",
                can_delete=True,
            )
        elif self._credential_store.has_session_credentials:
            self.settings_page.show_credential_status(
                "API Key 仅保留在当前程序会话，关闭软件后将清除。",
                can_delete=True,
            )
        elif self._credential_store.last_warning:
            self.settings_page.show_credential_status(
                self._credential_store.last_warning,
                can_delete=False,
            )
        else:
            self.settings_page.show_credential_status(
                "尚未保存 API Key。",
                can_delete=False,
            )

    @Slot()
    def delete_saved_credentials(self) -> None:
        deleted = self._credential_store.delete_all()
        self._refresh_credential_status()
        self.footer_status.setText(
            "已删除保存的 API Key"
            if deleted
            else "当前没有可删除的 API Key"
        )

    @Slot()
    def start_trial_translation(self) -> None:
        self._start_trial(target_ids=None)

    @Slot()
    def retry_failed_trial_samples(self) -> None:
        if self.trial_result is None or not self.trial_result.failed_ids:
            self._show_action_blocked("当前没有失败的试译条目可重试。")
            return
        self._start_trial(target_ids=self.trial_result.failed_ids)

    def _start_trial(
        self,
        *,
        target_ids: frozenset[str] | None,
    ) -> None:
        if self._task_running() or self._close_when_idle:
            self._show_action_blocked(
                self._active_task_reason()
                or "软件正在安全关闭，暂时不能开始试译。"
            )
            return
        if (
            self.current_inspection is None
            or self.translation_session_config is None
            or not self.trial_samples
            or not self._trial_task_id
        ):
            self._show_action_blocked(
                "请先完成翻译配置并准备试译样本。"
            )
            return
        task = TrialTranslationTask(
            self.current_inspection.input_directory,
            self.translation_session_config,
            self.trial_samples,
            self._trial_service,
            task_id=self._trial_task_id,
            target_ids=target_ids,
        )
        if not self._begin_task(
            task,
            TaskKind.TRIAL_TRANSLATION,
            "小批量试译",
        ):
            return
        self._trial_running = True
        self._refresh_step_navigation()
        self.trial_translation_page.show_running(
            retry=target_ids is not None
        )
        self.footer_status.setText("正在进行小批量试译")
        task.signals.progress.connect(self._trial_progress)
        task.signals.completed.connect(self._trial_completed)
        task.signals.failed.connect(self._trial_failed)
        self._thread_pool.start(task)

    @Slot(object)
    def _trial_progress(self, event: TrialProgressEvent) -> None:
        if self._trial_running:
            self.workflow_controller.update_task_progress(
                f"{event.completed:,}/{event.total:,} · {event.message}"
            )
            self.trial_translation_page.update_progress(event)

    @Slot(object)
    def _trial_completed(self, result: TrialTranslationResult) -> None:
        self._trial_running = False
        self._finish_task()
        self._refresh_step_navigation()
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
        self._finish_task()
        self._refresh_step_navigation()
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

    @Slot(str)
    def mark_trial_sample_satisfied(self, text_id: str) -> None:
        if self.current_inspection is None:
            return
        csv_path = project_paths(
            self.current_inspection.input_directory
        ).extracted_csv
        try:
            update_translation_review_record(
                csv_path,
                text_id,
                ReviewAction.APPROVE,
            )
        except (OSError, ValueError):
            self.trial_translation_page.show_feedback(
                "未能保存确认状态，译文内容没有被改动。",
                error=True,
            )
            return
        self.trial_translation_page.show_feedback(
            "已标记满意，人工确认将优先于后续 AI 与缓存结果。"
        )

    @Slot(object)
    def save_trial_feedback(self, payload: object) -> None:
        if self.current_inspection is None or not isinstance(payload, dict):
            return
        text_id = payload.get("text_id")
        reason = payload.get("reason")
        scope_value = payload.get("scope")
        instruction = payload.get("instruction")
        if not all(
            isinstance(value, str)
            for value in (text_id, reason, scope_value, instruction)
        ):
            self.trial_translation_page.show_feedback(
                "修改要求格式无效，请重新填写。",
                error=True,
            )
            return
        instruction = instruction.strip()
        if not instruction:
            self.trial_translation_page.show_feedback(
                "请先填写希望怎样修改这条译文。",
                error=True,
            )
            return
        try:
            scope = TranslationRuleScope(scope_value)
        except ValueError:
            self.trial_translation_page.show_feedback(
                "请选择有效的应用范围。",
                error=True,
            )
            return
        paths = project_paths(self.current_inspection.input_directory)
        try:
            records = read_extracted_csv(paths.extracted_csv)
            record = next(item for item in records if item.id == text_id)
        except (OSError, ValueError, StopIteration):
            self.trial_translation_page.show_feedback(
                "找不到对应的扫描记录，请重新扫描后再试。",
                error=True,
            )
            return
        rule_store = self._translation_rule_store(paths.translation_rules_json)
        rule_type = {
            "wording": TranslationRuleType.EXACT,
            "tone": TranslationRuleType.STYLE,
            "terminology": TranslationRuleType.TERMINOLOGY,
            "machine": TranslationRuleType.STYLE,
            "custom": TranslationRuleType.STYLE,
        }.get(reason, TranslationRuleType.STYLE)
        try:
            rule = rule_store.add_feedback_rule(
                record,
                rule_type=rule_type,
                scope=scope,
                instruction=instruction,
                source=f"trial_feedback:{reason}",
            )
            update_translation_review_record(
                paths.extracted_csv,
                text_id,
                ReviewAction.NEEDS_RETRANSLATE,
            )
        except (OSError, ValueError):
            if "rule" in locals():
                try:
                    rule_store.set_enabled(rule.rule_id, False)
                except (OSError, ValueError):
                    pass
            self.trial_translation_page.show_feedback(
                "修改要求未能完整保存，原译文仍可在检查页处理。",
                error=True,
            )
            return
        self._mark_trial_sample_for_retry(text_id)
        resolution = rule_store.resolve(record)
        conflict_text = (
            f"；另有 {len(resolution.conflicts)} 条冲突规则未采用"
            if resolution.conflicts
            else ""
        )
        self.trial_translation_page.show_feedback(
            f"修改要求已保存到“{_scope_label(scope)}”，"
            f"该样本已加入重试列表{conflict_text}。"
        )

    def _mark_trial_sample_for_retry(self, text_id: str) -> None:
        if self.trial_result is None:
            return
        samples = tuple(
            replace(
                sample,
                translation="",
                status=TrialSampleStatus.FAILED,
                from_cache=False,
            )
            if sample.text_id == text_id
            else sample
            for sample in self.trial_result.samples
        )
        self.trial_result = replace(self.trial_result, samples=samples)
        self.trial_samples = samples
        self.trial_translation_page.show_result(
            TrialPageViewModel.from_result(self.trial_result)
        )

    def _translation_rule_store(
        self,
        project_path: Path,
    ) -> TranslationRuleStore:
        global_path = (
            self._settings_path.parent / "translation_rules.json"
            if self._settings_path is not None
            else None
        )
        return TranslationRuleStore(
            project_path,
            global_path=global_path,
        )

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
            self._show_action_blocked(self._active_task_reason())
            return
        self.stage = WorkflowStage.TRIAL_TRANSLATION
        self._show_page(self.trial_translation_page)
        self.footer_status.setText("试译完成")

    @Slot()
    def start_full_translation(self) -> None:
        self._start_full_translation(self._full_pending_ids)

    @Slot()
    def retry_failed_full_translation(self) -> None:
        if self._full_result is None or not self._full_result.failed_ids:
            self._show_action_blocked("当前没有失败的完整翻译条目可重试。")
            return
        self._start_full_translation(self._full_result.failed_ids)

    def _start_full_translation(
        self,
        target_ids: frozenset[str],
    ) -> None:
        if self._task_running() or self._close_when_idle:
            self._show_action_blocked(
                self._active_task_reason()
                or "软件正在安全关闭，暂时不能开始完整翻译。"
            )
            return
        if (
            self.current_inspection is None
            or self.translation_session_config is None
            or not self._full_selected_ids
            or not target_ids
        ):
            self._show_action_blocked(
                "请先确认试译结果，并确保仍有待翻译条目。"
            )
            return
        paths = project_paths(self.current_inspection.input_directory)
        try:
            current_records = read_extracted_csv(paths.extracted_csv)
        except (OSError, UnicodeError, ValueError):
            self.full_translation_page.show_failure(
                "无法读取扫描清单，请返回扫描页面重新扫描。"
            )
            return
        task = FullTranslationTask(
            self.current_inspection.input_directory,
            self.translation_session_config,
            selected_ids=self._full_selected_ids,
            target_ids=target_ids,
            task_id=self._full_task_id,
            translator_factory=self._full_translator_factory,
        )
        if not self._begin_task(
            task,
            TaskKind.FULL_TRANSLATION,
            "完整翻译",
            cancel=task.request_stop,
            pause=task.pause,
            resume=task.resume,
        ):
            return
        self._full_translated_before_run = sum(
            record.id in self._full_selected_ids
            and bool(record.translation.strip())
            for record in current_records
        )
        self._full_running = True
        self._refresh_step_navigation()
        self._full_started_at = perf_counter()
        self._full_usage = self._read_full_usage(paths.usage_sqlite)
        self._full_activity.append("已开始完整翻译，成功批次会立即保存。")
        self.full_translation_page.show_activity(
            tuple(self._full_activity)
        )
        self.full_translation_page.show_running(
            retry=(
                self._full_result is not None
                and target_ids == self._full_result.failed_ids
            )
        )
        self.footer_status.setText("正在进行完整翻译")
        task.signals.progress.connect(self._full_progress)
        task.signals.translation_event.connect(
            self._full_translation_event
        )
        task.signals.completed.connect(self._full_completed)
        task.signals.failed.connect(self._full_failed)
        self._full_task = task
        self._thread_pool.start(task)

    @Slot(object)
    def _full_progress(self, progress: TranslationProgress) -> None:
        if not self._full_running:
            return
        self._full_last_progress = progress
        self.workflow_controller.update_task_progress(
            f"{progress.completed_rows:,}/{progress.total_rows:,} · "
            f"成功 {progress.translated_rows:,} · 失败 {progress.failed_rows:,}"
        )
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
                usage=self._full_usage,
                budget=self._budget_limit_usd,
            )
        )

    @Slot(object)
    def _full_translation_event(self, event: object) -> None:
        if not self._full_running:
            return
        if isinstance(event, TranslationStarted):
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
            self._append_full_activity(
                f"正在处理 {self._full_current_category} · "
                f"{event.file_path or event.text_id}"
            )
        elif isinstance(event, TranslationItemCompleted):
            preview = " ".join(event.translation.split())
            self._append_full_activity(
                f"{event.status} · {event.file_path or event.text_id} · "
                f"{preview[:80]}"
            )
        elif isinstance(event, TranslationItemFailed):
            self._append_full_activity(
                f"需要处理 · {event.file_path or event.text_id}"
            )
        elif isinstance(event, TranslationBatchCompleted):
            if self.current_inspection is None:
                return
            usage_path = project_paths(
                self.current_inspection.input_directory
            ).usage_sqlite
            self._full_usage = self._read_full_usage(usage_path)
            self._apply_budget_boundary()
            if self._full_last_progress is not None:
                self._full_progress(self._full_last_progress)

    def _append_full_activity(self, message: str) -> None:
        self._full_activity.append(message)
        del self._full_activity[:-100]
        self.full_translation_page.show_activity(
            tuple(self._full_activity)
        )

    def _read_full_usage(self, path: Path) -> TranslationUsageSummary:
        if not Path(path).is_file() or not self._full_task_id:
            return TranslationUsageSummary()
        try:
            with UsageLedger(path) as ledger:
                return UsageQueryService(ledger).task_summary(
                    self._full_task_id
                )
        except (OSError, sqlite3.Error, ValueError):
            return TranslationUsageSummary()

    def _apply_budget_boundary(self) -> None:
        budget = self._budget_limit_usd
        if budget is None or budget <= 0:
            return
        amount = (
            self._full_usage.reported_cost_total
            if self._full_usage.reported_cost_total is not None
            else self._full_usage.estimated_cost
        )
        if amount is None:
            return
        if (
            amount >= budget
            and self.session.active_task is not None
            and not self.session.active_task.paused
        ):
            if self.workflow_controller.pause_active_task():
                self.full_translation_page.show_pause_requested()
                self._append_full_activity(
                    "已达到预算上限，当前请求完成后已安全暂停。"
                )
                self.footer_status.setText("已达到预算上限并安全暂停")
            return
        if (
            not self._full_budget_warning_emitted
            and amount >= budget * Decimal("0.8")
        ):
            self._full_budget_warning_emitted = True
            self._append_full_activity("费用已达到预算的 80%。")

    @Slot(object)
    def _full_completed(
        self,
        result: FullTranslationTaskResult,
    ) -> None:
        self._full_running = False
        self._finish_task()
        self._full_task = None
        self._refresh_step_navigation()
        self._full_elapsed_previous += result.elapsed_seconds
        self._full_result = result
        self._full_usage = result.usage
        self._full_pending_ids = result.remaining_ids
        view_model = FullTranslationPageViewModel.from_result(
            result,
            current_category=self._full_current_category,
            elapsed_seconds=self._full_elapsed_previous,
            budget=self._budget_limit_usd,
        )
        self.full_translation_page.show_result(view_model)
        self.footer_status.setText("完整翻译任务结束")
        if view_model.is_complete:
            self.show_translation_review()
        self._close_if_pending()

    @Slot(object)
    def _full_failed(self, failure: TaskFailure) -> None:
        self._full_running = False
        self._finish_task()
        self._full_task = None
        self._refresh_step_navigation()
        self.full_translation_page.show_failure(failure.message)
        self.footer_status.setText("完整翻译未能继续")
        self._close_if_pending()

    @Slot()
    def pause_full_translation(self) -> None:
        if self._full_task is None or not self._full_running:
            self._show_action_blocked("当前没有可暂停的完整翻译任务。")
            return
        if not self.workflow_controller.pause_active_task():
            self._show_action_blocked("当前翻译任务暂时无法暂停。")
            return
        self.full_translation_page.show_pause_requested()
        self.footer_status.setText("将在当前批次完成后暂停")

    @Slot()
    def resume_full_translation(self) -> None:
        if self._full_task is None or not self._full_running:
            self._show_action_blocked("当前没有已暂停的完整翻译任务。")
            return
        if not self.workflow_controller.resume_active_task():
            self._show_action_blocked("当前翻译任务尚未暂停。")
            return
        self.full_translation_page.show_resumed()
        self.footer_status.setText("继续完整翻译")

    @Slot()
    def show_full_translation_result(self) -> None:
        self._show_full_translation_page()

    @Slot()
    def show_translation_review(self) -> None:
        self._review_unlocked = True
        self.stage = WorkflowStage.TRANSLATION_REVIEW
        self._show_page(self.translation_review_page)
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
            self._translation_review_view_model(records, issues)
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
            success_message="已加入重译列表，本轮没有调用 API。",
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
            self._translation_review_view_model(records, issues),
            selected_id=record_id,
        )
        self.translation_review_page.show_feedback(success_message)

    def _translation_review_view_model(
        self,
        records,
        issues,
    ) -> TranslationReviewPageViewModel:
        cost = (
            self._full_usage.reported_cost_total
            if self._full_usage.reported_cost_total is not None
            else self._full_usage.estimated_cost
        )
        currency = (
            self._full_usage.reported_cost_currency
            if self._full_usage.reported_cost_total is not None
            else self._full_usage.estimated_cost_currency
        )
        return TranslationReviewPageViewModel.from_data(
            records,
            issues,
            cost_amount=cost,
            cost_currency=currency,
        )

    @Slot(str)
    def show_retranslate_placeholder(self, _record_id: str) -> None:
        self.translation_review_page.show_feedback(
            "重新翻译当前项将在下一批接入，本轮没有调用 API。"
        )

    @Slot()
    def show_build_install(self) -> None:
        if self._build_running:
            self._show_action_blocked(self._active_task_reason())
            return
        self._build_unlocked = True
        self.stage = WorkflowStage.BUILD_INSTALL
        self._show_page(self.build_install_page)
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
            self._show_action_blocked(
                self._active_task_reason()
                or "请先完成译文检查并确认当前整合包。"
            )
            return
        paths = project_paths(self.current_inspection.input_directory)
        task = BuildTask(
            modpack_dir=self.current_inspection.input_directory,
            csv_path=paths.extracted_csv,
            output_dir=paths.output_dir,
            minecraft_version=self.current_inspection.minecraft_version,
            service=self._build_service,
        )
        if not self._begin_task(task, TaskKind.BUILD, "生成资源包"):
            return
        self._build_running = True
        self._refresh_step_navigation()
        self.build_install_page.show_running("正在生成资源包")
        self.footer_status.setText("正在生成资源包")
        task.signals.completed.connect(self._build_completed)
        task.signals.failed.connect(self._build_operation_failed)
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
            self._show_action_blocked(
                self._active_task_reason()
                or "请先成功生成可安装的资源包。"
            )
            return
        task = ExportTask(
            output_dir=self._build_result.output_dir,
            service=self._export_service,
        )
        if not self._begin_task(task, TaskKind.EXPORT, "导出 ZIP"):
            return
        self._build_running = True
        self._refresh_step_navigation()
        self.build_install_page.show_running("正在导出 ZIP")
        self.footer_status.setText("正在导出 ZIP")
        task.signals.completed.connect(self._export_completed)
        task.signals.failed.connect(self._build_operation_failed)
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
            self._show_action_blocked(
                self._active_task_reason()
                or "请先成功生成可安装的资源包。"
            )
            return
        task = InstallTask(
            modpack_dir=self.current_inspection.input_directory,
            output_dir=self._build_result.output_dir,
            service=self._install_service,
        )
        if not self._begin_task(task, TaskKind.INSTALL, "安装汉化包"):
            return
        self._build_running = True
        self._refresh_step_navigation()
        self.build_install_page.show_running("正在安装汉化包")
        self.footer_status.setText("正在安装汉化包")
        task.signals.completed.connect(self._install_completed)
        task.signals.failed.connect(self._build_operation_failed)
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
            self._show_action_blocked(
                self._active_task_reason()
                or "当前没有可撤销的安装记录。"
            )
            return
        task = RollbackTask(
            modpack_dir=self.current_inspection.input_directory,
            backup_dir=self._install_result.backup_dir,
            service=self._rollback_service,
        )
        if not self._begin_task(task, TaskKind.ROLLBACK, "撤销安装"):
            return
        self._build_running = True
        self._refresh_step_navigation()
        self.completion_page.show_running("正在撤销本次安装")
        self.footer_status.setText("正在撤销本次安装")
        task.signals.completed.connect(self._rollback_completed)
        task.signals.failed.connect(self._completion_operation_failed)
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
        self._show_page(self.build_install_page)
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
        self._show_page(self.completion_page)
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
        self._finish_task()
        self._refresh_step_navigation()

    @staticmethod
    def _open_directory(path: Path) -> bool:
        return QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(path))
        )

    def _show_full_translation_page(self) -> None:
        self.stage = WorkflowStage.FULL_TRANSLATION
        self._show_page(self.full_translation_page)
        self.footer_status.setText("等待完整翻译")

    def _choose_directory(self) -> str | None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "选择 Minecraft 整合包目录",
            str(self._last_directory or ""),
            QFileDialog.Option.ShowDirsOnly,
        )
        return selected or None

    def _refresh_home_projects(self) -> None:
        self._recent_projects = self._recent_projects_store.load()
        self._last_directory = self._recent_projects.last_directory
        manual_paths = tuple(
            project.path for project in self._recent_projects.projects
        )
        try:
            self._discovered_projects = self._project_discovery_service(
                manual_paths
            )
        except (OSError, RuntimeError, ValueError):
            self._discovered_projects = ()
        if hasattr(self, "home_page"):
            self.home_page.show_projects(
                self._recent_projects.projects,
                self._discovered_projects,
            )

    def _remember_inspection(self, inspection: ModpackInspection) -> None:
        previous = self._recent_projects_store.find(
            inspection.input_directory
        )
        project = RecentProject.from_inspection(
            inspection,
            current_stage=self.stage.value,
            last_page=self.session.current_page,
            previous=previous,
        )
        try:
            self._recent_projects = self._recent_projects_store.upsert(
                project,
                last_directory=inspection.input_directory,
            )
        except OSError:
            self.footer_status.setText(
                "检测完成，但最近项目未能保存"
            )
            return
        self._last_directory = inspection.input_directory
        self.home_page.show_projects(
            self._recent_projects.projects,
            self._discovered_projects,
        )

    @Slot()
    def _persist_project_progress(self) -> None:
        inspection = self.current_inspection
        if inspection is None:
            return
        try:
            self._recent_projects = (
                self._recent_projects_store.update_progress(
                    inspection.input_directory,
                    current_stage=self.stage.value,
                    last_page=self.session.current_page,
                )
            )
        except OSError:
            self.footer_status.setText(
                "当前流程可继续，但项目进度未能保存"
            )

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._task_running():
            event.ignore()
            if self._close_when_idle:
                return
            task = self.session.active_task
            decision = self._close_decision_provider(
                task.label if task is not None else "当前任务",
                bool(task is not None and task.cancellable),
            )
            if decision is CloseDecision.ABANDON_CLOSE:
                return
            if decision is CloseDecision.CANCEL_TASK:
                if not self.workflow_controller.cancel_active_task():
                    self._show_action_blocked(
                        "当前任务暂不支持立即取消，请选择继续后台等待，"
                        "或放弃关闭后稍后再试。"
                    )
                    return
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
        if self._owns_theme_manager:
            self.theme_manager.dispose()
        super().closeEvent(event)

    def _disable_task_starters_for_pending_close(self) -> None:
        self.home_page.select_button.setEnabled(False)
        self.inspection_page.reselect_button.setEnabled(False)
        self.inspection_page.start_scan_button.setEnabled(False)
        self.scan_page.rescan_button.setEnabled(False)
        self.scan_page.continue_button.setEnabled(False)
        self.translation_plan_page.back_button.setEnabled(False)
        self.translation_plan_page.advanced_button.setEnabled(False)
        self.translation_plan_page.continue_button.setEnabled(False)
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
        self._refresh_disabled_reasons()

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
        return self.workflow_controller.task_running

    def _pending_close_message(self) -> str:
        if self._trial_running:
            return "正在安全结束当前试译，完成后将自动关闭"
        if self._full_running:
            return "正在保存当前翻译批次，完成后将自动关闭"
        if self._build_running:
            return "正在完成当前文件操作，完成后将自动关闭"
        return "正在安全结束当前扫描，完成后将自动关闭"

    def _prompt_close_decision(
        self,
        task_label: str,
        cancellable: bool,
    ) -> CloseDecision:
        dialog = QMessageBox(self)
        dialog.setWindowTitle("任务仍在运行")
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setText(f"{task_label}仍在运行，请选择关闭方式。")
        dialog.setInformativeText(
            "继续后台等待会在任务结束后自动关闭；"
            "取消任务会请求安全停止；放弃关闭会返回程序。"
        )
        wait_button = dialog.addButton(
            "继续后台等待",
            QMessageBox.ButtonRole.AcceptRole,
        )
        cancel_button = dialog.addButton(
            "取消任务",
            QMessageBox.ButtonRole.DestructiveRole,
        )
        abandon_button = dialog.addButton(
            "放弃关闭",
            QMessageBox.ButtonRole.RejectRole,
        )
        cancel_button.setToolTip(
            "请求任务在安全边界停止"
            if cancellable
            else "当前任务暂不支持立即取消"
        )
        cancel_button.setEnabled(cancellable)
        dialog.exec()
        clicked = dialog.clickedButton()
        if clicked is wait_button:
            return CloseDecision.WAIT_IN_BACKGROUND
        if clicked is cancel_button:
            return CloseDecision.CANCEL_TASK
        if clicked is abandon_button:
            return CloseDecision.ABANDON_CLOSE
        return CloseDecision.ABANDON_CLOSE

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
        self._full_usage = TranslationUsageSummary()
        self._full_activity = []
        self._full_budget_warning_emitted = False
        self._full_last_progress = None

    def _clear_build_state(self) -> None:
        self._build_running = False
        self._build_result = None
        self._install_result = None
        self._export_result = None


def _saved_plan_mode(settings: UserSettings) -> TranslationPlanMode:
    try:
        return TranslationPlanMode(
            settings.plan_mode or TranslationPlanMode.BALANCED.value
        )
    except ValueError:
        return TranslationPlanMode.BALANCED


def _saved_theme_preference(settings: UserSettings) -> ThemePreference:
    try:
        return ThemePreference(
            settings.theme_mode or ThemePreference.SYSTEM.value
        )
    except ValueError:
        return ThemePreference.SYSTEM


def _saved_budget(settings: UserSettings) -> Decimal | None:
    if not settings.budget_limit_usd:
        return None
    try:
        budget = Decimal(settings.budget_limit_usd)
    except (InvalidOperation, ValueError):
        return None
    return budget if budget > 0 else None


def _scope_label(scope: TranslationRuleScope) -> str:
    return {
        TranslationRuleScope.RECORD: "当前文本",
        TranslationRuleScope.FILE: "当前文件",
        TranslationRuleScope.MOD: "当前模组",
        TranslationRuleScope.PROJECT: "当前项目",
        TranslationRuleScope.GLOBAL: "全局相似内容",
    }[scope]


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
    window = MainWindow(
        recent_projects_store=RecentProjectsStore(),
        project_discovery_service=(
            lambda manual_paths: discover_modpacks(
                manual_paths=manual_paths
            )
        ),
        credential_store=CredentialStore(),
        settings_path=config_path(),
    )
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
                window.translation_plan_page,
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
