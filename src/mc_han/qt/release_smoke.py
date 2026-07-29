from __future__ import annotations

import ctypes
import hashlib
import json
import os
import sys
import threading
import time
import zipfile
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication, QPushButton

from mc_han.core.project import project_paths
from mc_han.csv_store import read_extracted_csv
from mc_han.qt.main_window import MainWindow
from mc_han.qt.theme import (
    EffectiveTheme,
    ThemeManager,
    ThemePreference,
)
from mc_han.qt.translation_config_view_models import (
    TranslationProvider,
    TranslationSessionConfig,
)
from mc_han.qt.view_models import WorkflowStage
from mc_han.services.project_discovery import DiscoveredProject
from mc_han.services.recent_projects import RecentProjectsStore
from mc_han.services.trial_translation import run_trial_translation
from mc_han.translator.base import TranslationSegment
from mc_han.translator.usage import (
    ProviderAttemptError,
    ProviderAttemptResult,
    UsageNormalizationResult,
)
from mc_han.usage.models import TokenUsage, UsageOutcome
from mc_han.workflow.translation_plan import TranslationPlanMode


class _ReleaseFakeProvider:
    is_network_provider = True
    provider_name = "release-fake"
    model = "release-fake-model"
    endpoint_type = "chat_completions"
    thinking_mode = ""

    def __init__(
        self,
        *,
        fail_once_original: str = "",
        failure_attempts: int = 1,
        delay_first: bool = False,
    ) -> None:
        self.fail_once_original = fail_once_original
        self.remaining_failures = failure_attempts
        self.delay_first = delay_first
        self.calls: list[tuple[str, ...]] = []
        self.first_started = threading.Event()

    def translate_batch_with_usage(
        self,
        segments: list[TranslationSegment],
    ) -> ProviderAttemptResult:
        self.calls.append(tuple(segment.id for segment in segments))
        if len(self.calls) == 1:
            self.first_started.set()
            if self.delay_first:
                time.sleep(0.3)
        if (
            self.fail_once_original
            and self.remaining_failures > 0
            and any(
                segment.text == self.fail_once_original
                for segment in segments
            )
        ):
            self.remaining_failures -= 1
            raise ProviderAttemptError(
                outcome=UsageOutcome.PROVIDER_ERROR,
                stable_error_code="release_fake_failure",
                retryable=False,
                usage=UsageNormalizationResult(
                    tokens=TokenUsage(
                        input_tokens=5,
                        output_tokens=0,
                        total_tokens=5,
                    ),
                    provider_reported_cost=Decimal("0"),
                    currency="USD",
                ),
            )
        return ProviderAttemptResult(
            translations=tuple(
                f"汉化文本 {index + 1}"
                for index, _segment in enumerate(segments)
            ),
            usage=UsageNormalizationResult(
                tokens=TokenUsage(
                    input_tokens=max(1, len(segments) * 5),
                    output_tokens=max(1, len(segments) * 3),
                    total_tokens=max(1, len(segments) * 8),
                ),
                provider_reported_cost=Decimal("0"),
                currency="USD",
            ),
            provider_request_id="release-smoke",
        )


def run_packaged_e2e_smoke_test() -> int:
    application = QApplication.instance() or QApplication([])
    application.setApplicationName("mc-han")
    application.setOrganizationName("mc-han")
    try:
        with TemporaryDirectory(prefix="mc-han-release-smoke-") as temporary:
            _run_e2e(application, Path(temporary))
    except (OSError, RuntimeError, ValueError, AssertionError, zipfile.BadZipFile) as error:
        print(
            f"mc-han packaged E2E smoke failed: {type(error).__name__}",
            file=sys.stderr,
        )
        return 1
    return 0


def _run_e2e(application: QApplication, root: Path) -> None:
    modpack = root / "test-modpack"
    jar_path = _create_test_modpack(modpack)
    jar_digest = _sha256(jar_path)
    installed_mcmeta = modpack / "resourcepacks" / "mc-han-cn" / "pack.mcmeta"
    installed_mcmeta.parent.mkdir(parents=True)
    original_mcmeta = b"original resource pack"
    installed_mcmeta.write_bytes(original_mcmeta)
    settings_path = root / "user" / "config.json"
    recent_store = RecentProjectsStore(root / "user" / "projects.json")
    system_theme = {"value": EffectiveTheme.LIGHT}
    theme_manager = ThemeManager(
        application,
        system_detector=lambda: system_theme["value"],
    )
    full_provider = _ReleaseFakeProvider(
        fail_once_original="Demo description 9",
        failure_attempts=3,
        delay_first=True,
    )

    def trial_service(path, config, samples, **kwargs):
        return run_trial_translation(
            path,
            config,
            samples,
            translator_factory=lambda _config: _ReleaseFakeProvider(),
            **kwargs,
        )

    window = MainWindow(
        directory_picker=lambda: str(modpack),
        trial_service=trial_service,
        full_translator_factory=lambda _config: full_provider,
        directory_opener=lambda _path: True,
        install_confirmation_provider=lambda _result: True,
        settings_path=settings_path,
        recent_projects_store=recent_store,
        project_discovery_service=lambda _manual: (
            DiscoveredProject(
                path=modpack,
                display_name="Release Smoke Pack",
                launcher="Release smoke",
                evidence=("manifest.json", "mods"),
            ),
        ),
        theme_manager=theme_manager,
    )
    window.show()
    application.processEvents()

    _require(
        window.home_page.discovered_list.count() == 1,
        "automatic project discovery",
    )
    window.settings_button.click()
    _require(window.pages.currentWidget() is window.settings_page, "settings page")
    _require(
        theme_manager.preference is ThemePreference.SYSTEM
        and theme_manager.effective_theme is EffectiveTheme.LIGHT,
        "system light theme",
    )
    _require_windows_title_bar_theme(application, window, dark=False)
    window.change_theme_preference("dark")
    application.processEvents()
    _require(
        theme_manager.preference is ThemePreference.DARK
        and theme_manager.effective_theme is EffectiveTheme.DARK,
        "system to manual dark theme",
    )
    _require_windows_title_bar_theme(application, window, dark=True)
    window.change_theme_preference("system")
    application.processEvents()
    _require(
        theme_manager.preference is ThemePreference.SYSTEM
        and theme_manager.effective_theme is EffectiveTheme.LIGHT,
        "manual dark to system light theme",
    )
    _require_windows_title_bar_theme(application, window, dark=False)
    window.settings_page.back_button.click()
    _require(window.pages.currentWidget() is window.home_page, "settings return")
    system_theme["value"] = EffectiveTheme.DARK
    window.settings_button.click()
    application.processEvents()
    _require(
        theme_manager.preference is ThemePreference.SYSTEM
        and theme_manager.effective_theme is EffectiveTheme.DARK,
        "system dark theme refresh",
    )
    _require_windows_title_bar_theme(application, window, dark=True)
    window.change_theme_preference("light")
    application.processEvents()
    _require(
        theme_manager.preference is ThemePreference.LIGHT
        and theme_manager.effective_theme is EffectiveTheme.LIGHT,
        "system dark to manual light theme",
    )
    _require_windows_title_bar_theme(application, window, dark=False)
    window.change_theme_preference("dark")
    application.processEvents()
    _require(
        theme_manager.preference is ThemePreference.DARK
        and theme_manager.effective_theme is EffectiveTheme.DARK,
        "manual light to manual dark theme",
    )
    _require_windows_title_bar_theme(application, window, dark=True)
    window.settings_page.back_button.click()
    _require(window.pages.currentWidget() is window.home_page, "theme settings return")

    discovered_row = window.home_page.discovered_list.itemAt(0).widget()
    _require(discovered_row is not None, "discovered project row")
    discovered_buttons = discovered_row.findChildren(QPushButton)
    _require(bool(discovered_buttons), "discovered project open button")
    discovered_buttons[0].click()
    _wait_for(
        application,
        lambda: window.stage is WorkflowStage.INSPECTION_RESULT,
        "inspection",
    )
    _require(
        window.current_inspection is not None
        and window.current_inspection.can_continue,
        "inspection result",
    )

    window.inspection_page.start_scan_button.click()
    _wait_for(
        application,
        lambda: window.stage is WorkflowStage.SCAN_RESULT,
        "scan",
    )
    _require(window.scan_page.continue_button.isEnabled(), "scan continue")
    window.scan_page.continue_button.click()
    _require(
        window.stage is WorkflowStage.TRANSLATION_PLAN,
        "translation plan",
    )
    _require(
        window.selected_translation_plan is not None
        and window.selected_translation_plan.mode
        is TranslationPlanMode.BALANCED,
        "balanced translation plan",
    )
    window.translation_plan_page.advanced_button.click()
    _require(
        window.stage is WorkflowStage.TRANSLATION_CONFIG,
        "translation config",
    )

    window.translation_config_page.set_config(
        TranslationSessionConfig(
            provider=TranslationProvider.OPENAI_COMPATIBLE,
            base_url="https://release-smoke.invalid/v1",
            model="release-fake-model",
            api_key="release-fake-key",
            concurrency=1,
            batch_size=1,
            timeout_seconds=30,
        )
    )
    window.translation_config_page.validate_button.click()
    _require(
        window.translation_config_page.validation_label.isVisible(),
        "config validation",
    )
    window.translation_config_page.continue_button.click()
    _require(
        window.stage is WorkflowStage.TRANSLATION_PLAN,
        "return to translation plan",
    )
    _require(
        settings_path.is_file()
        and "release-fake-key"
        not in settings_path.read_text(encoding="utf-8"),
        "API key not stored in plaintext settings",
    )
    window.translation_plan_page.continue_button.click()
    _require(
        window.stage is WorkflowStage.TRIAL_TRANSLATION,
        "trial page",
    )

    window.trial_translation_page.start_button.click()
    _wait_for(
        application,
        lambda: (
            window.trial_result is not None
            and not window._trial_running
        ),
        "trial translation",
    )
    _require(
        window.trial_translation_page.continue_button.isEnabled(),
        "trial continue",
    )
    feedback_id = window.trial_result.samples[0].text_id
    window.save_trial_feedback(
        {
            "text_id": feedback_id,
            "reason": "terminology",
            "scope": "project",
            "instruction": "保留 ME 作为专有术语",
        }
    )
    _require(
        window.trial_result.failed_ids == frozenset({feedback_id}),
        "trial feedback retry scope",
    )
    window.retry_failed_trial_samples()
    _wait_for(
        application,
        lambda: (
            window.trial_result is not None
            and not window.trial_result.failed_ids
            and not window._trial_running
        ),
        "trial feedback retry",
    )
    window.trial_translation_page.continue_button.click()
    _require(
        window.stage is WorkflowStage.FULL_TRANSLATION,
        "full translation page",
    )
    if window.full_translation_page.start_button.isEnabled():
        window.full_translation_page.start_button.click()
    _wait_for(
        application,
        full_provider.first_started.is_set,
        "full translation first request",
    )
    window.settings_button.click()
    _require(
        window.pages.currentWidget() is window.settings_page
        and window.stage is WorkflowStage.FULL_TRANSLATION,
        "settings during translation",
    )
    window.settings_page.back_button.click()
    _require(
        window.pages.currentWidget() is window.full_translation_page,
        "return to active translation",
    )
    window.full_translation_page.pause_button.click()
    _require(
        window.session.active_task is not None
        and window.session.active_task.paused,
        "translation pause",
    )
    window.full_translation_page.resume_button.click()
    _require(
        window.session.active_task is not None
        and not window.session.active_task.paused,
        "translation resume",
    )
    _wait_for(
        application,
        lambda: (
            window._full_result is not None
            and not window._full_running
        ),
        "full translation",
    )
    _require(
        len(window._full_result.failed_ids) == 1,
        (
            "one failed full translation record "
            f"(failed={len(window._full_result.failed_ids)}, "
            f"calls={len(full_provider.calls)})"
        ),
    )
    _require(
        window._full_result.total_count == 18
        and window._full_result.successful_count == 17,
        (
            "nine new successes and one failed request "
            f"(success={window._full_result.successful_count}, "
            f"total={window._full_result.total_count})"
        ),
    )
    failed_ids = window._full_result.failed_ids
    window.full_translation_page.retry_button.click()
    _wait_for(
        application,
        lambda: window.stage is WorkflowStage.TRANSLATION_REVIEW,
        "retry failed full translation record",
    )
    _require(
        full_provider.calls[-1] == tuple(failed_ids),
        "only failed full translation record retried",
    )

    review = window.translation_review_page
    review.filter_combo.setCurrentIndex(
        review.filter_combo.findData("all")
    )
    application.processEvents()
    _require(review.table_model.rowCount() > 0, "review records")
    review.table.selectRow(0)
    application.processEvents()
    review.translation_editor.setPlainText("人工审核译文")
    review.save_button.click()
    application.processEvents()
    review.approve_button.click()
    application.processEvents()
    selected_id = review.selected_text_id
    saved = {record.id: record for record in read_extracted_csv(
        project_paths(modpack).extracted_csv
    )}
    _require(
        selected_id in saved
        and saved[selected_id].translation == "人工审核译文"
        and saved[selected_id].review_status == "approved",
        "review save",
    )

    review.continue_button.click()
    _require(window.stage is WorkflowStage.BUILD_INSTALL, "build page")
    window.build_install_page.build_button.click()
    _wait_for(
        application,
        lambda: window._build_result is not None and not window._build_running,
        "resource pack build",
    )
    _require(
        window.build_install_page.export_button.isEnabled()
        and window.build_install_page.install_button.isEnabled(),
        "build actions",
    )
    output_dir = project_paths(modpack).output_dir
    zh_cn = (
        output_dir
        / "resourcepacks"
        / "mc-han-cn"
        / "assets"
        / "demo"
        / "lang"
        / "zh_cn.json"
    )
    _require(
        zh_cn.is_file()
        and "汉化" in zh_cn.read_text(encoding="utf-8"),
        "zh_cn output",
    )

    window.build_install_page.export_button.click()
    _wait_for(
        application,
        lambda: (
            window.stage is WorkflowStage.COMPLETION
            and window._export_result is not None
        ),
        "ZIP export",
    )
    archive_path = window._export_result.archive_path
    with zipfile.ZipFile(archive_path) as archive:
        _require(
            "resourcepacks/mc-han-cn/assets/demo/lang/zh_cn.json"
            in archive.namelist(),
            "ZIP contents",
        )

    window.completion_page.back_button.click()
    _require(window.stage is WorkflowStage.BUILD_INSTALL, "return to build")
    window.build_install_page.install_button.click()
    _wait_for(
        application,
        lambda: (
            window.stage is WorkflowStage.COMPLETION
            and window._install_result is not None
            and not window._build_running
        ),
        "installation",
    )
    installed_zh_cn = (
        modpack
        / "resourcepacks"
        / "mc-han-cn"
        / "assets"
        / "demo"
        / "lang"
        / "zh_cn.json"
    )
    _require(installed_zh_cn.is_file(), "installed zh_cn")
    _require(installed_mcmeta.read_bytes() != original_mcmeta, "installed overwrite")

    _wait_for(
        application,
        lambda: window._thread_pool.activeThreadCount() == 0,
        "first window worker shutdown",
    )
    window.close()
    application.processEvents()
    theme_manager.dispose()

    window = MainWindow(
        directory_picker=lambda: str(modpack),
        directory_opener=lambda _path: True,
        install_confirmation_provider=lambda _result: True,
        settings_path=settings_path,
        recent_projects_store=recent_store,
        project_discovery_service=lambda _manual: (),
    )
    window.show()
    application.processEvents()
    _require(
        window.theme_manager.preference is ThemePreference.DARK
        and window.theme_manager.effective_theme is EffectiveTheme.DARK,
        "manual dark theme restart persistence",
    )
    _require(
        window.home_page.continue_button.isVisible(),
        "restart recent project",
    )
    window.home_page.continue_button.click()
    _wait_for(
        application,
        lambda: (
            window.stage is WorkflowStage.INSPECTION_RESULT
            and window._install_result is not None
        ),
        "restart install history recovery",
    )
    window.workflow_step_buttons[2].click()
    _require(
        window.stage is WorkflowStage.COMPLETION
        and window.completion_page.rollback_button.isVisible(),
        "restart rollback entry",
    )
    window.completion_page.rollback_button.click()
    _wait_for(
        application,
        lambda: (
            window._install_result is None
            and not window._build_running
        ),
        "installation rollback",
    )
    _require(installed_mcmeta.read_bytes() == original_mcmeta, "rollback restore")
    _require(not installed_zh_cn.exists(), "rollback remove")
    _require(_sha256(jar_path) == jar_digest, "JAR read-only")

    _wait_for(
        application,
        lambda: window._thread_pool.activeThreadCount() == 0,
        "worker shutdown",
    )
    window.close()
    application.processEvents()


def _create_test_modpack(modpack: Path) -> Path:
    modpack.mkdir(parents=True)
    (modpack / "config").mkdir()
    (modpack / "manifest.json").write_text(
        json.dumps(
            {
                "manifestType": "minecraftModpack",
                "name": "Release Smoke Pack",
                "minecraft": {
                    "version": "1.20.4",
                    "modLoaders": [
                        {"id": "neoforge-20.4.200", "primary": True}
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    jar_path = modpack / "mods" / "demo.jar"
    jar_path.parent.mkdir()
    entries = {
        f"demo.description.{index}": f"Demo description {index}"
        for index in range(18)
    }
    with zipfile.ZipFile(
        jar_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        archive.writestr(
            "assets/demo/lang/en_us.json",
            json.dumps(entries),
        )
    return jar_path


def _wait_for(
    application: QApplication,
    predicate,
    label: str,
    *,
    timeout: float = 30.0,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        if predicate():
            return
        QThread.msleep(5)
    raise RuntimeError(f"{label} timed out")


def _require(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)


def _require_windows_title_bar_theme(
    application: QApplication,
    window: MainWindow,
    *,
    dark: bool,
) -> None:
    if os.name != "nt" or application.platformName().casefold() != "windows":
        return
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        application.processEvents()
        enabled = ctypes.c_int()
        result = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            int(window.winId()),
            20,
            ctypes.byref(enabled),
            ctypes.sizeof(enabled),
        )
        if result == 0 and bool(enabled.value) is dark:
            return
        QThread.msleep(10)
    _require(False, "Windows title bar theme")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
