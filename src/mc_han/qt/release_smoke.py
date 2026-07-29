from __future__ import annotations

import hashlib
import json
import sys
import time
import zipfile
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication

from mc_han.core.project import project_paths
from mc_han.csv_store import read_extracted_csv
from mc_han.qt.main_window import MainWindow
from mc_han.qt.translation_config_view_models import (
    TranslationProvider,
    TranslationSessionConfig,
)
from mc_han.qt.view_models import WorkflowStage
from mc_han.services.trial_translation import run_trial_translation
from mc_han.translator.base import TranslationSegment
from mc_han.translator.usage import (
    ProviderAttemptResult,
    UsageNormalizationResult,
)
from mc_han.usage.models import TokenUsage


class _ReleaseFakeProvider:
    is_network_provider = True
    provider_name = "release-fake"
    model = "release-fake-model"
    endpoint_type = "chat_completions"
    thinking_mode = ""

    def translate_batch_with_usage(
        self,
        segments: list[TranslationSegment],
    ) -> ProviderAttemptResult:
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
        full_translator_factory=lambda _config: _ReleaseFakeProvider(),
        directory_opener=lambda _path: True,
    )
    window.show()
    application.processEvents()

    window.settings_button.click()
    _require(window.pages.currentWidget() is window.settings_page, "settings page")
    window.settings_page.back_button.click()
    _require(window.pages.currentWidget() is window.home_page, "settings return")

    window.home_page.select_button.click()
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
            batch_size=10,
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
    window.trial_translation_page.continue_button.click()
    _require(
        window.stage is WorkflowStage.FULL_TRANSLATION,
        "full translation page",
    )
    if window.full_translation_page.start_button.isEnabled():
        window.full_translation_page.start_button.click()
    _wait_for(
        application,
        lambda: window.stage is WorkflowStage.TRANSLATION_REVIEW,
        "full translation",
    )

    review = window.translation_review_page
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
        for index in range(12)
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
