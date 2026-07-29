from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication

from mc_han.builder.installer import InstallResult, RollbackResult
from mc_han.qt.main_window import MainWindow
from mc_han.qt.view_models import WorkflowStage
from mc_han.services.build_install import (
    BuildWorkflowResult,
    ExportWorkflowResult,
)
from mc_han.services.install_history import InstallHistoryStore
from mc_han.workflow.models import (
    CAPABILITY_ORDER,
    ChineseResourceStatus,
    ContentCapability,
    ExistingChineseResources,
    InspectionValidity,
    LoaderInfo,
    ModpackInspection,
)


@pytest.fixture(scope="module")
def application():
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


def _inspection(path: Path) -> ModpackInspection:
    return ModpackInspection(
        input_directory=path,
        validity=InspectionValidity.VALID,
        display_name="Build Pack",
        minecraft_version="1.20.4",
        loader=LoaderInfo("NeoForge", "47.1.0"),
        mod_count=1,
        capabilities=tuple(
            ContentCapability(key, key, detected=False)
            for key in CAPABILITY_ORDER
        ),
        existing_chinese=ExistingChineseResources(
            ChineseResourceStatus.NONE
        ),
        messages=(),
        evidence=(),
        inspection_duration=0.1,
    )


def _build_result(output_dir: Path) -> BuildWorkflowResult:
    return BuildWorkflowResult(
        output_dir=output_dir,
        output_file_name="mc-han-cn.zip",
        resource_files=3,
        config_files=2,
        translated_rows=12,
        installable_files=7,
        new_files=5,
        overwrite_files=2,
        pack_format=22,
        warnings=("english_residue: assets/demo/page.json",),
        errors=(),
    )


def _process_until(
    application: QApplication,
    predicate,
    *,
    timeout: float = 5,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        if predicate():
            return
        QThread.msleep(5)
    raise AssertionError("Qt condition was not reached before timeout")


def _close(application: QApplication, window: MainWindow) -> None:
    _process_until(
        application,
        lambda: window._thread_pool.activeThreadCount() == 0,
    )
    window.close()
    application.processEvents()


def test_qt_build_and_export_run_in_background(
    application: QApplication,
    tmp_path: Path,
):
    output_dir = tmp_path / ".mc-han" / "output"
    output_dir.mkdir(parents=True)
    started = threading.Event()
    release = threading.Event()
    worker_threads: list[bool] = []
    opened: list[Path] = []

    def build_service(**kwargs):
        worker_threads.append(
            QThread.currentThread() is application.thread()
        )
        assert kwargs["minecraft_version"] == "1.20.4"
        started.set()
        release.wait(2)
        return _build_result(output_dir)

    def export_service(**kwargs):
        worker_threads.append(
            QThread.currentThread() is application.thread()
        )
        archive = kwargs["output_dir"] / "mc-han-cn.zip"
        archive.write_bytes(b"zip")
        return ExportWorkflowResult(archive, 3)

    window = MainWindow(
        build_service=build_service,
        export_service=export_service,
        directory_opener=lambda path: opened.append(path) or True,
    )
    window.current_inspection = _inspection(tmp_path)
    window.show_build_install()
    window.show()

    window.build_install_page.build_button.click()
    _process_until(application, started.is_set)
    assert window._build_running
    assert not window.build_install_page.back_button.isEnabled()
    assert worker_threads == [False]
    release.set()
    _process_until(
        application,
        lambda: window._build_result is not None,
    )

    page = window.build_install_page
    assert page.summary_values["name"].text() == "mc-han-cn.zip"
    assert page.summary_values["entries"].text() == "7"
    assert page.summary_values["pack_format"].text() == "22"
    assert "english_residue" in page.diagnostics_label.text()
    assert page.export_button.isEnabled()
    assert page.install_button.isEnabled()
    page.open_button.click()
    assert opened == [output_dir]

    page.export_button.click()
    _process_until(
        application,
        lambda: window.stage is WorkflowStage.COMPLETION,
    )
    assert worker_threads == [False, False]
    assert window.completion_page.title.text() == "ZIP 已导出"
    assert not window.completion_page.rollback_button.isVisible()
    _close(application, window)


def test_qt_install_and_rollback_use_existing_manifest_results(
    application: QApplication,
    tmp_path: Path,
):
    output_dir = tmp_path / ".mc-han" / "output"
    output_dir.mkdir(parents=True)
    backup_dir = tmp_path / ".mc-han" / "backups" / "one"
    backup_dir.mkdir(parents=True)
    manifest = backup_dir / "install_manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    calls: list[str] = []

    def install_service(**_kwargs):
        calls.append("install")
        return InstallResult(7, 2, backup_dir, manifest)

    def rollback_service(**kwargs):
        calls.append("rollback")
        assert kwargs["backup_dir"] == backup_dir
        return RollbackResult(2, 5, backup_dir, manifest)

    window = MainWindow(
        install_service=install_service,
        rollback_service=rollback_service,
        install_confirmation_provider=lambda _result: True,
    )
    window.current_inspection = _inspection(tmp_path)
    window._build_result = _build_result(output_dir)
    window.show_build_install()

    window.build_install_page.install_button.click()
    _process_until(
        application,
        lambda: window.stage is WorkflowStage.COMPLETION,
    )
    assert calls == ["install"]
    assert window.completion_page.title.text() == "汉化包已安装"
    assert not window.completion_page.rollback_button.isHidden()

    window.completion_page.rollback_button.click()
    _process_until(
        application,
        lambda: calls == ["install", "rollback"]
        and not window._build_running,
    )
    assert window.completion_page.title.text() == "本次安装已撤销"
    assert window.completion_page.rollback_button.isHidden()
    _close(application, window)


def test_qt_restores_persistent_rollback_after_reopening_project(
    application: QApplication,
    tmp_path: Path,
):
    modpack = tmp_path / "pack"
    backup_dir = modpack / ".mc-han" / "backups" / "one"
    backup_dir.mkdir(parents=True)
    target = modpack / "resourcepacks" / "mc-han-cn" / "pack.mcmeta"
    target.parent.mkdir(parents=True)
    target.write_text("installed", encoding="utf-8")
    manifest = backup_dir / "install_manifest.json"
    manifest.write_text(
        (
            '{"version":1,"created_at":"2026-07-29T10:00:00+00:00",'
            '"items":[{"relative_target":'
            '"resourcepacks/mc-han-cn/pack.mcmeta",'
            '"had_backup":false,"backup_relative":""}]}'
        ),
        encoding="utf-8",
    )
    result = InstallResult(1, 0, backup_dir, manifest)
    InstallHistoryStore(modpack).record_install(result)

    window = MainWindow()
    inspection = _inspection(modpack)
    window.current_inspection = inspection
    window._restore_install_history(modpack)
    window._remember_inspection(inspection)

    assert window._install_result is not None
    assert window._build_unlocked
    assert not window.completion_page.rollback_button.isHidden()
    recent = window._recent_projects_store.find(modpack)
    assert recent is not None
    assert recent.installed
    assert recent.can_rollback
    _close(application, window)


def test_install_confirmation_can_cancel_without_starting_task(
    application: QApplication,
    tmp_path: Path,
):
    output_dir = tmp_path / ".mc-han" / "output"
    output_dir.mkdir(parents=True)
    calls: list[str] = []

    window = MainWindow(
        install_service=lambda **_kwargs: calls.append("install"),
        install_confirmation_provider=lambda result: (
            calls.append(f"confirm:{result.new_files}") or False
        ),
    )
    window.current_inspection = _inspection(tmp_path)
    window._build_result = _build_result(output_dir)
    window.show_build_install()

    window.build_install_page.install_button.click()
    application.processEvents()

    assert calls == ["confirm:5"]
    assert not window._build_running
    assert "已取消安装" in window.build_install_page.feedback_label.text()
    _close(application, window)


def test_qt_build_failure_keeps_prior_result_available(
    application: QApplication,
    tmp_path: Path,
):
    output_dir = tmp_path / ".mc-han" / "output"
    output_dir.mkdir(parents=True)

    def failing_build(**_kwargs):
        raise RuntimeError("fake")

    window = MainWindow(build_service=failing_build)
    window.current_inspection = _inspection(tmp_path)
    window._build_result = _build_result(output_dir)
    window.show_build_install()
    window.start_build()
    _process_until(
        application,
        lambda: not window._build_running,
    )

    assert window._build_result is not None
    assert window.build_install_page.export_button.isEnabled()
    assert "已有成功输出" in window.build_install_page.feedback_label.text()
    _close(application, window)
