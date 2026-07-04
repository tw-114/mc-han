from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from typing import Any
from tkinter import filedialog, messagebox, ttk

from .config import DEFAULT_NAME_TRANSLATION_FORMAT
from .builder.installer import install_outputs, plan_install_outputs, rollback_install, write_install_plan_report
from .builder.resourcepack import (
    build_client_resourcepack,
    build_complete_install_package,
    build_outputs,
    build_server_pack,
)
from .checkpoints import create_checkpoint, csv_status, rollback_checkpoint
from .core.project import (
    ensure_project_dirs,
    project_paths,
    read_scan_options,
    write_extracted_jsonl,
    write_scan_options,
)
from .csv_store import read_extracted_csv, write_extracted_csv
from .quality.checks import check_csv, check_output_dir, write_quality_report
from .review import write_review_report
from .scanner import merge_existing_translations, scan_modpack, write_scan_report
from .settings import UserSettings, clear_settings, config_path, load_settings, save_settings
from .translator.base import TranslationSegment
from .translator.engine import (
    TranslationBatchCompleted,
    TranslationBatchStarted,
    TranslationItemCompleted,
    TranslationItemFailed,
    TranslationProgress,
    TranslationStarted,
    translate_csv,
)
from .translator.mock_provider import MockTranslator
from .translator.openai_provider import OpenAICompatibleTranslator, PROVIDER_PRESETS

CUSTOM_PROVIDER = "custom"
SPEED_LABELS = {
    "稳定": "safe",
    "平衡": "balanced",
    "快速": "fast",
}
SPEED_LABEL_BY_MODE = {value: key for key, value in SPEED_LABELS.items()}
SCAN_SCOPE_CHANGED_MESSAGE = "扫描范围已改变，需要重新扫描。旧翻译缓存会保留，不会浪费已翻译内容。"


class McHanApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("mc-han")
        self.geometry("920x680")
        self.minsize(760, 560)
        self.queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.pause_event = threading.Event()
        self.stop_event = threading.Event()

        self.modpack_dir = tk.StringVar()
        self.output_dir = tk.StringVar()
        self.provider = tk.StringVar(value="mock")
        self.model = tk.StringVar()
        self.api_key = tk.StringVar()
        self.api_key_env = tk.StringVar()
        self.base_url = tk.StringVar()
        self.limit = tk.StringVar()
        self.speed_mode_label = tk.StringVar(value="平衡")
        self.worker_count = tk.StringVar(value="1")
        self.max_batch_items = tk.StringVar()
        self.max_input_tokens = tk.StringVar()
        self.max_output_tokens = tk.StringVar()
        self.translate_names = tk.BooleanVar(value=False)
        self.name_translation_format = tk.StringVar(value=DEFAULT_NAME_TRANSLATION_FORMAT)
        self.status = tk.StringVar(value="就绪")
        self.install_after_build = tk.BooleanVar(value=False)
        self.total_rows = tk.StringVar(value="总待翻译：0")
        self.ftb_rows = tk.StringVar(value="FTB Quests：0")
        self.guide_rows = tk.StringVar(value="GuideME/AE2：0")
        self.book_rows = tk.StringVar(value="Patchouli/Modonomicon：0")
        self.item_name_rows = tk.StringVar(value="item.*：0")
        self.block_name_rows = tk.StringVar(value="block.*：0")
        self.entity_name_rows = tk.StringVar(value="entity.*：0")
        self.fluid_name_rows = tk.StringVar(value="fluid.*：0")
        self.name_scan_warning = tk.StringVar(value="")
        self.translated_rows = tk.StringVar(value="已翻译：0")
        self.cache_rows = tk.StringVar(value="缓存/复用：0")
        self.api_rows = tk.StringVar(value="API新翻译：0")
        self.failed_rows = tk.StringVar(value="失败：0")
        self.remaining_rows = tk.StringVar(value="剩余：0")
        self.eta = tk.StringVar(value="预计剩余：--")
        self.current_text_id = tk.StringVar(value="text_id：--")
        self.current_file = tk.StringVar(value="文件：--")
        self.current_translation_status = tk.StringVar(value="状态：--")
        self.current_batch = tk.StringVar(value="当前批次：--")
        self.current_original = tk.StringVar(value="原文：--")
        self.current_translation = tk.StringVar(value="译文：--")
        self.preview_filter = tk.StringVar(value="最近完成")
        self.realtime_rows: list[dict[str, str]] = []
        self.realtime_limit = 200
        self.current_batch_ids: set[str] = set()
        self.current_file_path = ""
        self.selected_text_id: str | None = None
        self.preview_render_scheduled = False
        self.scan_dirty = False
        self.last_scan_translate_names: bool | None = None
        self._suppress_translate_names_dirty = False

        self._apply_settings(load_settings(), quiet=True)
        self._build_ui()
        self._poll_queue()

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(5, weight=1)
        ttk.Style(self).configure("Danger.TLabel", foreground="#b00020")

        form = ttk.Frame(self, padding=14)
        form.grid(row=0, column=0, sticky="ew")
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="整合包目录").grid(row=0, column=0, sticky="w", padx=(0, 10), pady=4)
        ttk.Entry(form, textvariable=self.modpack_dir).grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Button(form, text="选择", command=self._choose_modpack).grid(row=0, column=2, padx=(8, 0), pady=4)

        ttk.Label(form, text="输出目录").grid(row=1, column=0, sticky="w", padx=(0, 10), pady=4)
        ttk.Entry(form, textvariable=self.output_dir).grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Button(form, text="选择", command=self._choose_output).grid(row=1, column=2, padx=(8, 0), pady=4)

        provider_frame = ttk.Frame(self, padding=(14, 0, 14, 8))
        provider_frame.grid(row=1, column=0, sticky="ew")
        for index in range(6):
            provider_frame.columnconfigure(index, weight=1 if index in {1, 3, 5} else 0)

        ttk.Label(provider_frame, text="Provider").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
        providers = ["mock", CUSTOM_PROVIDER, *sorted(PROVIDER_PRESETS)]
        provider_box = ttk.Combobox(
            provider_frame,
            textvariable=self.provider,
            values=providers,
            state="readonly",
            width=16,
        )
        provider_box.grid(row=0, column=1, sticky="ew", pady=4)
        provider_box.bind("<<ComboboxSelected>>", self._on_provider_change)
        ttk.Label(provider_frame, text="模型").grid(row=0, column=2, sticky="w", padx=(12, 8), pady=4)
        ttk.Entry(provider_frame, textvariable=self.model).grid(row=0, column=3, sticky="ew", pady=4)
        ttk.Label(provider_frame, text="翻译速度").grid(row=0, column=4, sticky="w", padx=(12, 8), pady=4)
        speed_box = ttk.Combobox(
            provider_frame,
            textvariable=self.speed_mode_label,
            values=list(SPEED_LABELS),
            state="readonly",
            width=10,
        )
        speed_box.grid(row=0, column=5, sticky="ew", pady=4)
        speed_box.bind("<<ComboboxSelected>>", self._on_speed_change)

        ttk.Label(provider_frame, text="API Key").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(provider_frame, textvariable=self.api_key, show="*").grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Label(provider_frame, text="环境变量").grid(row=1, column=2, sticky="w", padx=(12, 8), pady=4)
        ttk.Entry(provider_frame, textvariable=self.api_key_env).grid(row=1, column=3, sticky="ew", pady=4)
        ttk.Label(provider_frame, text="Base URL").grid(row=1, column=4, sticky="w", padx=(12, 8), pady=4)
        ttk.Entry(provider_frame, textvariable=self.base_url).grid(row=1, column=5, sticky="ew", pady=4)

        ttk.Label(provider_frame, text="并发").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Combobox(
            provider_frame,
            textvariable=self.worker_count,
            values=("1", "2", "3"),
            state="readonly",
            width=8,
        ).grid(row=2, column=1, sticky="ew", pady=4)
        ttk.Label(provider_frame, text="batch_size").grid(row=2, column=2, sticky="w", padx=(12, 8), pady=4)
        ttk.Entry(provider_frame, textvariable=self.max_batch_items, width=10).grid(row=2, column=3, sticky="ew", pady=4)
        ttk.Label(provider_frame, text="token_limit").grid(row=2, column=4, sticky="w", padx=(12, 8), pady=4)
        ttk.Entry(provider_frame, textvariable=self.max_input_tokens, width=10).grid(row=2, column=5, sticky="ew", pady=4)

        ttk.Label(provider_frame, text="高级设置").grid(row=3, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Label(provider_frame, text="output_token").grid(row=3, column=2, sticky="w", padx=(12, 8), pady=4)
        ttk.Entry(provider_frame, textvariable=self.max_output_tokens, width=10).grid(row=3, column=3, sticky="ew", pady=4)
        ttk.Button(provider_frame, text="保存配置", command=self._save_config).grid(
            row=3, column=1, sticky="ew", pady=(4, 0)
        )
        ttk.Button(provider_frame, text="加载配置", command=self._load_config).grid(
            row=3, column=4, sticky="ew", pady=(4, 0)
        )
        ttk.Button(provider_frame, text="清除配置", command=self._clear_config).grid(
            row=3, column=5, sticky="ew", pady=(4, 0)
        )
        ttk.Checkbutton(
            provider_frame,
            text="翻译物品/方块/实体/流体名称，并保留英文原名",
            variable=self.translate_names,
            command=self._on_translate_names_change,
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(6, 2))
        ttk.Label(provider_frame, text="名称格式").grid(row=4, column=3, sticky="w", padx=(12, 8), pady=(6, 2))
        ttk.Entry(provider_frame, textvariable=self.name_translation_format).grid(
            row=4, column=4, columnspan=2, sticky="ew", pady=(6, 2)
        )
        ttk.Label(
            provider_frame,
            text="开启后建议所有玩家使用同一个客户端资源包，否则多人联机时看到的物品名称可能不同。",
        ).grid(row=5, column=0, columnspan=6, sticky="w", pady=(0, 4))

        actions = ttk.Frame(self, padding=(14, 0, 14, 8))
        actions.grid(row=2, column=0, sticky="ew")
        for index in range(5):
            actions.columnconfigure(index, weight=1)
        buttons = [
            ("扫描", lambda: self._run_worker(self._scan)),
            ("重新扫描", lambda: self._run_worker(self._scan)),
            ("试翻译10条", lambda: self._run_worker(self._trial_translate)),
            ("继续翻译", self._confirm_continue_translate),
            ("暂停", self._pause_translation),
            ("恢复", self._resume_translation),
            ("停止", self._stop_translation),
            ("生成客户端资源包", lambda: self._run_worker(self._build_client_pack)),
            ("生成服务端任务包", lambda: self._run_worker(self._build_server_pack)),
            ("生成完整安装包", lambda: self._run_worker(self._build_all_outputs)),
            ("安装预演", lambda: self._run_worker(self._dry_run_install)),
            ("安装", self._confirm_install),
            ("回滚安装", self._confirm_install_rollback),
            ("测试API", lambda: self._run_worker(self._test_api_connection)),
            ("审阅报告", lambda: self._run_worker(self._review)),
            ("打开输出目录", self._open_output_dir),
        ]
        for index in range(6):
            actions.columnconfigure(index, weight=1)
        for index, (label, command) in enumerate(buttons):
            ttk.Button(actions, text=label, command=command).grid(
                row=index // 6,
                column=index % 6,
                sticky="ew",
                padx=(0 if index % 6 == 0 else 8, 0),
                pady=3,
            )

        stats_frame = ttk.Frame(self, padding=(14, 0, 14, 8))
        stats_frame.grid(row=3, column=0, sticky="ew")
        for index in range(5):
            stats_frame.columnconfigure(index, weight=1)
        for index, variable in enumerate(
            (
                self.total_rows,
                self.ftb_rows,
                self.guide_rows,
                self.book_rows,
                self.translated_rows,
            )
        ):
            ttk.Label(stats_frame, textvariable=variable).grid(row=0, column=index, sticky="w", padx=(0, 8))
        for index, variable in enumerate(
            (
                self.cache_rows,
                self.api_rows,
                self.failed_rows,
                self.remaining_rows,
                self.eta,
            )
        ):
            ttk.Label(stats_frame, textvariable=variable).grid(row=1, column=index, sticky="w", padx=(0, 8))
        for index, variable in enumerate(
            (
                self.item_name_rows,
                self.block_name_rows,
                self.entity_name_rows,
                self.fluid_name_rows,
            )
        ):
            ttk.Label(stats_frame, textvariable=variable).grid(row=2, column=index, sticky="w", padx=(0, 8))
        ttk.Label(stats_frame, textvariable=self.name_scan_warning, style="Danger.TLabel").grid(
            row=2,
            column=4,
            sticky="w",
            padx=(0, 8),
        )

        status_frame = ttk.Frame(self, padding=(14, 0, 14, 8))
        status_frame.grid(row=4, column=0, sticky="ew")
        status_frame.columnconfigure(0, weight=1)
        ttk.Label(status_frame, textvariable=self.status).grid(row=0, column=0, sticky="w")
        self.progress = ttk.Progressbar(status_frame, mode="indeterminate", length=180)
        self.progress.grid(row=0, column=1, sticky="e")

        content = ttk.PanedWindow(self, orient="vertical")
        content.grid(row=5, column=0, sticky="nsew", padx=14, pady=(0, 14))

        preview_frame = ttk.Frame(content)
        preview_frame.rowconfigure(2, weight=1)
        preview_frame.columnconfigure(0, weight=1)

        current_frame = ttk.LabelFrame(preview_frame, text="当前正在翻译")
        current_frame.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        for index in range(3):
            current_frame.columnconfigure(index, weight=1)
        ttk.Label(current_frame, textvariable=self.current_batch).grid(row=0, column=0, sticky="w", padx=8, pady=2)
        ttk.Label(current_frame, textvariable=self.current_text_id).grid(row=0, column=1, sticky="w", padx=8, pady=2)
        ttk.Label(current_frame, textvariable=self.current_translation_status).grid(row=0, column=2, sticky="w", padx=8, pady=2)
        ttk.Label(current_frame, textvariable=self.current_file).grid(row=1, column=0, columnspan=3, sticky="w", padx=8, pady=2)
        ttk.Label(current_frame, textvariable=self.current_original).grid(row=2, column=0, columnspan=3, sticky="w", padx=8, pady=2)
        ttk.Label(current_frame, textvariable=self.current_translation).grid(row=3, column=0, columnspan=3, sticky="w", padx=8, pady=2)

        filter_frame = ttk.Frame(preview_frame)
        filter_frame.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        ttk.Label(filter_frame, text="表格筛选").grid(row=0, column=0, sticky="w", padx=(0, 8))
        filter_box = ttk.Combobox(
            filter_frame,
            textvariable=self.preview_filter,
            values=("全部", "正在翻译", "最近完成", "失败", "疑似问题", "当前文件", "当前批次"),
            state="readonly",
            width=14,
        )
        filter_box.grid(row=0, column=1, sticky="w")
        filter_box.bind("<<ComboboxSelected>>", lambda _event: self._render_preview())

        self.preview = ttk.Treeview(
            preview_frame,
            columns=("id", "status", "source", "file", "original", "translation", "note"),
            show="headings",
            height=8,
        )
        for column, label, width in (
            ("id", "text_id", 90),
            ("status", "状态", 90),
            ("source", "来源", 120),
            ("file", "文件", 180),
            ("original", "原文", 260),
            ("translation", "译文", 260),
            ("note", "问题/备注", 140),
        ):
            self.preview.heading(column, text=label)
            self.preview.column(column, width=width, anchor="w")
        self.preview.grid(row=2, column=0, sticky="nsew")
        preview_scroll = ttk.Scrollbar(preview_frame, orient="vertical", command=self.preview.yview)
        preview_scroll.grid(row=2, column=1, sticky="ns")
        self.preview.configure(yscrollcommand=preview_scroll.set)
        self.preview.bind("<<TreeviewSelect>>", self._on_preview_select)

        detail_frame = ttk.LabelFrame(preview_frame, text="完整原文 / 完整译文")
        detail_frame.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        detail_frame.columnconfigure(0, weight=1)
        detail_frame.columnconfigure(1, weight=1)
        self.original_detail = tk.Text(detail_frame, wrap="word", height=4)
        self.translation_detail = tk.Text(detail_frame, wrap="word", height=4)
        self.original_detail.grid(row=0, column=0, sticky="ew", padx=(6, 3), pady=6)
        self.translation_detail.grid(row=0, column=1, sticky="ew", padx=(3, 6), pady=6)

        review_actions = ttk.Frame(preview_frame)
        review_actions.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        for index in range(4):
            review_actions.columnconfigure(index, weight=1)
        ttk.Button(review_actions, text="标记通过", command=self._mark_selected_passed).grid(row=0, column=0, sticky="ew")
        ttk.Button(review_actions, text="标记需要重翻", command=self._mark_selected_needs_retranslate).grid(
            row=0, column=1, sticky="ew", padx=(8, 0)
        )
        ttk.Button(review_actions, text="编辑译文", command=self._edit_selected_translation).grid(
            row=0, column=2, sticky="ew", padx=(8, 0)
        )
        ttk.Button(review_actions, text="重新翻译选中项", command=self._retranslate_selected).grid(
            row=0, column=3, sticky="ew", padx=(8, 0)
        )

        log_frame = ttk.Frame(content)
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self.log = tk.Text(log_frame, wrap="word", height=10)
        self.log.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scrollbar.set)

        content.add(preview_frame, weight=2)
        content.add(log_frame, weight=1)

    def _choose_modpack(self) -> None:
        path = filedialog.askdirectory(title="选择 Minecraft 整合包目录")
        if path:
            self.modpack_dir.set(path)
            if not self.output_dir.get().strip():
                self.output_dir.set(str(project_paths(Path(path)).output_dir))
            self._load_existing_state(Path(path))

    def _choose_output(self) -> None:
        path = filedialog.askdirectory(title="选择输出目录")
        if path:
            self.output_dir.set(path)

    def _save_config(self) -> None:
        try:
            settings = UserSettings(
                provider=self.provider.get().strip() or "mock",
                model=self.model.get().strip() or None,
                api_key=self.api_key.get().strip() or None,
                api_key_env=self.api_key_env.get().strip() or None,
                base_url=self.base_url.get().strip() or None,
                limit=self._parse_limit(),
                speed_mode=self._selected_speed_mode(),
                worker_count=self._parse_worker_count(),
                max_batch_items=self._parse_optional_int(self.max_batch_items, "batch_size"),
                max_input_tokens=self._parse_optional_int(self.max_input_tokens, "token_limit"),
                max_output_tokens=self._parse_optional_int(self.max_output_tokens, "output_token"),
                translate_names=self.translate_names.get(),
                name_translation_format=self._name_translation_format(),
            )
            saved_path = save_settings(settings)
        except Exception as error:  # noqa: BLE001 - GUI should show bad local settings.
            messagebox.showerror("mc-han", str(error))
            return
        self._log(f"已保存本地配置：{saved_path}")
        messagebox.showinfo("mc-han", f"配置已保存到：\n{saved_path}")

    def _load_config(self) -> None:
        settings = load_settings()
        self._apply_settings(settings)
        self._mark_scan_dirty_if_needed(show_dialog=True)
        self._log(f"已加载本地配置：{config_path()}")

    def _clear_config(self) -> None:
        if not messagebox.askyesno("mc-han", "清除会删除本地保存的 Provider/API Key/模型配置。继续吗？"):
            return
        deleted = clear_settings()
        self._log(f"已清除本地配置：{config_path()}" if deleted else f"没有找到本地配置：{config_path()}")

    def _apply_settings(self, settings: UserSettings, *, quiet: bool = False) -> None:
        if settings.provider:
            provider = settings.provider if settings.provider in {"mock", CUSTOM_PROVIDER, *PROVIDER_PRESETS} else CUSTOM_PROVIDER
            self.provider.set(provider)
        if settings.model:
            self.model.set(settings.model)
        if settings.api_key:
            self.api_key.set(settings.api_key)
        if settings.api_key_env:
            self.api_key_env.set(settings.api_key_env)
        if settings.base_url:
            self.base_url.set(settings.base_url)
        if settings.limit:
            self.limit.set(str(settings.limit))
        if settings.speed_mode:
            self.speed_mode_label.set(SPEED_LABEL_BY_MODE.get(settings.speed_mode, "平衡"))
        if settings.worker_count:
            self.worker_count.set(str(settings.worker_count))
        if settings.max_batch_items:
            self.max_batch_items.set(str(settings.max_batch_items))
        if settings.max_input_tokens:
            self.max_input_tokens.set(str(settings.max_input_tokens))
        if settings.max_output_tokens:
            self.max_output_tokens.set(str(settings.max_output_tokens))
        if settings.translate_names is not None:
            self.translate_names.set(settings.translate_names)
        if settings.name_translation_format:
            self.name_translation_format.set(settings.name_translation_format)
        if not quiet:
            self.status.set("已加载本地配置")

    def _run_worker(self, target) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("mc-han", "当前任务还在运行。")
            return
        self.status.set("运行中")
        self.progress.configure(mode="indeterminate", value=0)
        self.progress.start(12)
        self.worker = threading.Thread(target=self._worker_wrapper, args=(target,), daemon=True)
        self.worker.start()

    def _worker_wrapper(self, target) -> None:
        try:
            target()
            self.queue.put(("done", "完成。"))
        except Exception as error:  # noqa: BLE001 - GUI should surface unexpected failures.
            self.queue.put(("error", str(error)))

    def _scan(self) -> None:
        modpack_dir = self._require_modpack()
        paths = project_paths(modpack_dir)
        ensure_project_dirs(paths)
        self.output_dir.set(str(paths.output_dir))
        translate_names = self.translate_names.get()
        self._log(f"{'重新扫描' if paths.extracted_csv.exists() else '扫描'}：{modpack_dir}")
        existing_records = read_extracted_csv(paths.extracted_csv) if paths.extracted_csv.exists() else []
        records = scan_modpack(modpack_dir, translate_names=translate_names)
        if existing_records:
            records = merge_existing_translations(records, existing_records)
        write_extracted_csv(records, paths.extracted_csv)
        write_extracted_jsonl(records, paths.extracted_jsonl)
        write_scan_options(paths.scan_options_json, translate_names=translate_names)
        self.last_scan_translate_names = translate_names
        self.scan_dirty = False
        write_scan_report(
            modpack_dir=modpack_dir,
            records=records,
            output_csv=paths.extracted_csv,
            report_path=paths.scan_report,
        )
        self._emit_records(records)
        self._log(f"提取 {len(records)} 条，写入 {paths.extracted_jsonl}")

    def _all(self) -> None:
        modpack_dir = self._require_modpack()
        self._scan()
        self._translate(limit=self._parse_limit(), label="一键翻译")
        self._build_all_outputs()
        if self.install_after_build.get():
            result = install_outputs(modpack_dir=modpack_dir, build_dir=project_paths(modpack_dir).output_dir)
            self._log(f"安装 {result.installed_files} 个文件，备份 {result.backed_up_files} 个文件")

    def _trial_translate(self) -> None:
        self._translate(limit=10, label="试翻译10条")

    def _continue_translate(self) -> None:
        self._translate(limit=None, label="继续翻译")

    def _confirm_continue_translate(self) -> None:
        if self.provider.get().strip() != "mock" and not messagebox.askyesno(
            "mc-han",
            "继续翻译会处理所有未翻译内容并调用你选择的 API。确认继续吗？",
        ):
            return
        self._run_worker(self._continue_translate)

    def _translate(self, *, limit: int | None, label: str) -> None:
        modpack_dir = self._require_modpack()
        paths = project_paths(modpack_dir)
        ensure_project_dirs(paths)
        self._ensure_scan_scope_current()
        if not paths.extracted_csv.exists():
            self._log("未找到扫描结果，先自动扫描。")
            self._scan()
        translator = self._create_translator()
        if paths.extracted_csv.exists():
            checkpoint = create_checkpoint(paths.extracted_csv, label=f"before-{self.provider.get().strip()}-translate")
            self._log(f"已创建 checkpoint：{checkpoint.path}")
        self.pause_event.clear()
        self.stop_event.clear()
        self._log(label)
        records, translated_count, cache_hits = translate_csv(
            input_csv=paths.extracted_csv,
            output_csv=paths.extracted_csv,
            translator=translator,
            cache_path=paths.translation_cache_jsonl,
            sqlite_cache_path=paths.translations_sqlite,
            speed_mode=self._selected_speed_mode(),
            worker_count=self._parse_worker_count(),
            max_batch_items=self._parse_optional_int(self.max_batch_items, "batch_size"),
            max_input_tokens=self._parse_optional_int(self.max_input_tokens, "token_limit"),
            max_output_tokens=self._parse_optional_int(self.max_output_tokens, "output_token"),
            name_translation_format=self._name_translation_format(),
            limit=limit,
            pause_event=self.pause_event,
            stop_event=self.stop_event,
            continue_on_error=True,
            event_callback=self._emit_translation_event,
        )
        write_extracted_jsonl(records, paths.extracted_jsonl)
        self._emit_records(records, cache_hits=cache_hits)
        self._log(
            f"{label}完成：新翻译 {translated_count} 条，缓存/复用 {cache_hits} 条，"
            f"SQLite：{paths.translations_sqlite}"
        )

    def _build_client_pack(self) -> None:
        modpack_dir = self._require_modpack()
        paths = project_paths(modpack_dir)
        self._require_translated_csv(paths)
        stats = build_client_resourcepack(
            modpack_dir=modpack_dir,
            csv_path=paths.extracted_csv,
            output_dir=paths.output_dir,
        )
        self._log(f"客户端资源包已生成：{paths.output_dir / 'mc-han-client-resourcepack'}")
        self._log(f"资源文件 {stats['resource_files']} 个")

    def _build_server_pack(self) -> None:
        modpack_dir = self._require_modpack()
        paths = project_paths(modpack_dir)
        self._require_translated_csv(paths)
        stats = build_server_pack(
            modpack_dir=modpack_dir,
            csv_path=paths.extracted_csv,
            output_dir=paths.output_dir,
        )
        self._log(f"服务端任务包已生成：{paths.output_dir / 'mc-han-server-pack'}")
        self._log(f"配置覆盖文件 {stats['config_files']} 个")

    def _build_all_outputs(self) -> None:
        modpack_dir = self._require_modpack()
        paths = project_paths(modpack_dir)
        self._require_translated_csv(paths)
        issues = check_csv(paths.extracted_csv)
        write_quality_report(issues, paths.quality_report_txt)
        errors = sum(1 for issue in issues if issue.severity == "error")
        if errors:
            raise RuntimeError("CSV 检查失败，已停止构建。")
        stats = build_outputs(modpack_dir=modpack_dir, csv_path=paths.extracted_csv, output_dir=paths.output_dir)
        self._log(f"生成资源文件 {stats['resource_files']} 个，配置覆盖文件 {stats['config_files']} 个")
        output_issues = check_output_dir(paths.output_dir)
        write_quality_report(output_issues, paths.quality_report_txt)
        output_errors = sum(1 for issue in output_issues if issue.severity == "error")
        if output_errors:
            raise RuntimeError("输出目录检查失败，已停止。")
        complete_root = build_complete_install_package(
            output_dir=paths.output_dir,
            translate_names=self.translate_names.get() or self._csv_has_name_rows(paths.extracted_csv),
        )
        self._log(f"完整安装包辅助文件已生成：{complete_root}")

    def _test_api_connection(self) -> None:
        translator = self._create_translator()
        self._log("测试 API 连接...")
        result = translator.translate_batch(
            [
                TranslationSegment(
                    id="api-test",
                    text="Open the quest book to begin.",
                    source_type="api_test",
                    file_path="",
                    key_path="",
                )
            ]
        )
        if not result or not result[0].strip():
            raise RuntimeError("API 返回了空结果。")
        self._log(f"API 测试成功：{truncate(result[0], 80)}")

    def _review(self) -> None:
        modpack_dir = self._require_modpack()
        paths = project_paths(modpack_dir)
        if not paths.extracted_csv.exists():
            raise RuntimeError(f"找不到扫描结果：{paths.extracted_jsonl}。请先扫描。")
        stats = write_review_report(
            csv_path=paths.extracted_csv,
            output_path=paths.output_dir / "translation_review.html",
            limit=1000,
        )
        self._log(
            f"审阅报告已生成：显示 {stats.displayed_rows}/{stats.total_rows} 条，"
            f"{stats.errors} error，{stats.warnings} warning"
        )
        self._log(str(paths.output_dir / "translation_review.html"))

    def _status(self) -> None:
        modpack_dir = self._require_modpack()
        paths = project_paths(modpack_dir)
        if not paths.extracted_csv.exists():
            raise RuntimeError(f"找不到扫描结果：{paths.extracted_jsonl}")
        status = csv_status(paths.extracted_csv)
        self._log(
            f"状态：总计 {status.total_rows}，已翻译 {status.translated_rows}，"
            f"未翻译 {status.missing_rows}，{status.errors} error，"
            f"{status.warnings} warning，checkpoint {status.checkpoint_count} 个"
        )

    def _confirm_rollback(self) -> None:
        if not messagebox.askyesno("mc-han", "回滚会用最近一次 checkpoint 覆盖当前 CSV。继续吗？"):
            return
        self._run_worker(self._rollback)

    def _rollback(self) -> None:
        modpack_dir = self._require_modpack()
        paths = project_paths(modpack_dir)
        restored = rollback_checkpoint(paths.extracted_csv)
        records = read_extracted_csv(paths.extracted_csv)
        write_extracted_jsonl(records, paths.extracted_jsonl)
        self._emit_records(records)
        self._log(f"已回滚扫描/翻译状态：{paths.extracted_jsonl}")
        self._log(f"来源 checkpoint：{restored.path}")

    def _check_build(self) -> None:
        modpack_dir = self._require_modpack()
        paths = project_paths(modpack_dir)
        issues = check_output_dir(paths.output_dir)
        write_quality_report(issues, paths.quality_report_txt)
        errors = sum(1 for issue in issues if issue.severity == "error")
        warnings = sum(1 for issue in issues if issue.severity == "warning")
        self._log(f"检查完成：{errors} error，{warnings} warning")

    def _confirm_install(self) -> None:
        if not messagebox.askyesno("mc-han", "安装会写入整合包目录，并自动备份已有文件。继续吗？"):
            return
        self._run_worker(self._install)

    def _dry_run_install(self) -> None:
        modpack_dir = self._require_modpack()
        paths = project_paths(modpack_dir)
        plan = plan_install_outputs(modpack_dir=modpack_dir, build_dir=paths.output_dir)
        report_path = paths.output_dir / "install_plan.txt"
        write_install_plan_report(plan, report_path)
        self._log(
            f"安装预演：将安装 {plan.total_files} 个文件，"
            f"其中覆盖 {plan.overwrite_files} 个，新建 {plan.new_files} 个"
        )
        self._log(f"目标整合包：{modpack_dir}")
        self._log("安装时会备份到：<整合包>\\.mc-han\\backups\\<时间戳>")
        for item in plan.items[:20]:
            action = "覆盖" if item.will_overwrite else "新建"
            self._log(f"[{action}] {item.relative_target}")
        if len(plan.items) > 20:
            self._log(f"还有 {len(plan.items) - 20} 个文件，详见：{report_path}")
        self._log(f"预演报告：{report_path}")

    def _confirm_install_rollback(self) -> None:
        if not messagebox.askyesno("mc-han", "将按最近一次安装备份回滚。继续吗？"):
            return
        self._run_worker(self._rollback_install)

    def _pause_translation(self) -> None:
        self.pause_event.set()
        self.status.set("已暂停，当前 API 批次结束后停住")
        self._log("已请求暂停。")

    def _resume_translation(self) -> None:
        self.pause_event.clear()
        self.status.set("继续运行")
        self._log("已恢复。")

    def _stop_translation(self) -> None:
        self.stop_event.set()
        self.pause_event.clear()
        self.status.set("正在停止，当前 API 批次结束后保存状态")
        self._log("已请求停止。")

    def _open_output_dir(self) -> None:
        try:
            modpack_dir = self._require_modpack()
            paths = project_paths(modpack_dir)
            ensure_project_dirs(paths)
            os.startfile(paths.output_dir)  # type: ignore[attr-defined]
        except Exception as error:  # noqa: BLE001 - GUI should surface OS open failures.
            messagebox.showerror("mc-han", str(error))

    def _install(self) -> None:
        modpack_dir = self._require_modpack()
        paths = project_paths(modpack_dir)
        result = install_outputs(modpack_dir=modpack_dir, build_dir=paths.output_dir)
        self._log(f"安装 {result.installed_files} 个文件，备份 {result.backed_up_files} 个文件")
        self._log(f"备份目录：{result.backup_dir}")
        self._log(f"安装清单：{result.manifest_path}")

    def _rollback_install(self) -> None:
        modpack_dir = self._require_modpack()
        result = rollback_install(modpack_dir=modpack_dir)
        self._log(f"已回滚安装：恢复 {result.restored_files} 个文件，移除 {result.removed_files} 个新建文件")
        self._log(f"使用备份：{result.backup_dir}")

    def _create_translator(self):
        provider = self.provider.get().strip()
        if provider == "mock":
            return MockTranslator()
        if provider != CUSTOM_PROVIDER and provider not in PROVIDER_PRESETS:
            raise RuntimeError(f"未知 Provider：{provider}")
        model = self.model.get().strip()
        if not model:
            raise RuntimeError("真实 Provider 需要填写模型名。")
        preset = PROVIDER_PRESETS.get(provider)
        env_name = self.api_key_env.get().strip() or (preset.api_key_env if preset else "MC_HAN_API_KEY")
        api_key = self.api_key.get().strip() or os.environ.get(env_name)
        if not api_key:
            raise RuntimeError(f"缺少 API Key，请填写 API Key 或设置环境变量 {env_name}。")
        base_url = self.base_url.get().strip() or (preset.base_url if preset else "")
        if not base_url:
            raise RuntimeError("自定义 Provider 需要填写 Base URL。")
        provider_name = provider if provider != CUSTOM_PROVIDER else f"{CUSTOM_PROVIDER}:{base_url.rstrip('/')}"
        return OpenAICompatibleTranslator(
            provider_name=provider_name,
            model=model,
            api_key=api_key,
            base_url=base_url,
        )

    def _on_provider_change(self, _event=None) -> None:
        provider = self.provider.get().strip()
        if provider == "mock":
            self.status.set("mock 模式不会调用 API")
            return
        preset = PROVIDER_PRESETS.get(provider)
        if not self.api_key_env.get().strip():
            self.api_key_env.set(preset.api_key_env if preset else "MC_HAN_API_KEY")
        if provider == CUSTOM_PROVIDER:
            self.status.set("自定义 Provider 需要填写 Base URL、模型和 API Key")
        else:
            self.status.set("真实 Provider 建议先点“试翻译10条”确认效果")

    def _on_speed_change(self, _event=None) -> None:
        mode = self._selected_speed_mode()
        if mode == "safe":
            self.status.set("稳定模式：更小批次，适合任务书和长指南")
        elif mode == "fast":
            self.status.set("快速模式：更大批次，失败会自动拆小重试")
        else:
            self.status.set("平衡模式：默认速度，兼顾批量和安全")

    def _on_translate_names_change(self) -> None:
        if self.translate_names.get():
            self.status.set("已开启名称翻译：将保留英文原名，并建议所有玩家使用同一客户端资源包")
        else:
            self.status.set("已关闭名称翻译：item/block/entity/fluid 等名称会继续跳过")
        self._mark_scan_dirty_if_needed(show_dialog=True)

    def _mark_scan_dirty_if_needed(self, *, show_dialog: bool = False) -> None:
        try:
            modpack_dir = self._require_modpack()
        except RuntimeError:
            return
        paths = project_paths(modpack_dir)
        if not paths.extracted_csv.exists():
            self.scan_dirty = False
            return
        if self.last_scan_translate_names is None:
            self.last_scan_translate_names = self._read_last_scan_translate_names(paths)
        dirty = self.last_scan_translate_names != self.translate_names.get()
        self.scan_dirty = dirty
        if dirty:
            self.status.set(SCAN_SCOPE_CHANGED_MESSAGE)
            self._log(SCAN_SCOPE_CHANGED_MESSAGE)
            if show_dialog:
                messagebox.showinfo("mc-han", SCAN_SCOPE_CHANGED_MESSAGE)
        else:
            self.status.set("扫描范围与当前设置一致")

    def _ensure_scan_scope_current(self) -> None:
        self._mark_scan_dirty_if_needed(show_dialog=False)
        if self.scan_dirty:
            raise RuntimeError(SCAN_SCOPE_CHANGED_MESSAGE)

    def _read_last_scan_translate_names(self, paths) -> bool:
        raw = read_scan_options(paths.scan_options_json)
        value = raw.get("translate_names")
        if isinstance(value, bool):
            return value
        if paths.extracted_csv.exists():
            return self._csv_has_name_rows(paths.extracted_csv)
        return False

    def _require_modpack(self) -> Path:
        raw = self.modpack_dir.get().strip()
        if not raw:
            raise RuntimeError("请选择整合包目录。")
        path = Path(raw).expanduser().resolve()
        if not path.is_dir():
            raise RuntimeError(f"整合包目录不存在：{path}")
        return path

    def _require_translated_csv(self, paths) -> None:
        if not paths.extracted_csv.exists():
            raise RuntimeError("找不到扫描结果，请先扫描。")
        records = read_extracted_csv(paths.extracted_csv)
        if not any(record.translation.strip() for record in records):
            raise RuntimeError("还没有译文，请先试翻译或继续翻译。")

    def _resolve_output_dir(self, modpack_dir: Path) -> Path:
        raw = self.output_dir.get().strip()
        output_dir = Path(raw).expanduser().resolve() if raw else project_paths(modpack_dir).output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.set(str(output_dir))
        return output_dir

    def _parse_limit(self) -> int | None:
        raw = self.limit.get().strip()
        if not raw:
            return None
        try:
            value = int(raw)
        except ValueError as error:
            raise RuntimeError("限制条数必须是整数。") from error
        return value if value > 0 else None

    def _selected_speed_mode(self) -> str:
        return SPEED_LABELS.get(self.speed_mode_label.get().strip(), "balanced")

    def _parse_worker_count(self) -> int:
        raw = self.worker_count.get().strip() or "1"
        try:
            value = int(raw)
        except ValueError as error:
            raise RuntimeError("并发必须是 1、2 或 3。") from error
        if value not in {1, 2, 3}:
            raise RuntimeError("并发必须是 1、2 或 3。")
        return value

    def _parse_optional_int(self, variable: tk.StringVar, label: str) -> int | None:
        raw = variable.get().strip()
        if not raw:
            return None
        try:
            value = int(raw)
        except ValueError as error:
            raise RuntimeError(f"{label} 必须是正整数。") from error
        return value if value > 0 else None

    def _name_translation_format(self) -> str:
        value = self.name_translation_format.get().strip() or DEFAULT_NAME_TRANSLATION_FORMAT
        if "{zh}" not in value or "{en}" not in value:
            raise RuntimeError("名称格式必须同时包含 {zh} 和 {en}。")
        return value

    def _shared_cache_path(self, modpack_dir: Path) -> Path:
        return project_paths(modpack_dir).translation_cache_jsonl

    def _emit_translate_progress(self, progress: TranslationProgress) -> None:
        self.queue.put(("progress", progress))

    def _emit_translation_event(self, event: object) -> None:
        self.queue.put(("translation_event", event))

    def _emit_records(self, records: list, *, cache_hits: int = 0) -> None:
        self.queue.put(("records", (records, cache_hits)))

    def _log(self, message: str) -> None:
        self.queue.put(("info", message))

    def _poll_queue(self) -> None:
        try:
            while True:
                level, message = self.queue.get_nowait()
                if level == "progress":
                    self._show_translate_progress(message)
                    continue
                if level == "translation_event":
                    self._handle_translation_event(message)
                    continue
                if level == "records":
                    records, cache_hits = message
                    self._update_scan_stats(records, cache_hits=cache_hits)
                    self._refresh_preview(records)
                    continue
                log_level = "info" if level == "done" else level
                self._append_log(f"[{log_level}] {message}")
                if level == "done":
                    self.progress.stop()
                    self.status.set("完成")
                if level == "error":
                    self.progress.stop()
                    self.status.set("出错")
                    messagebox.showerror("mc-han", message)
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _append_log(self, message: str) -> None:
        self.log.insert("end", message + "\n")
        self.log.see("end")

    def _show_translate_progress(self, progress: TranslationProgress) -> None:
        total = max(1, progress.total_rows)
        completed = min(progress.completed_rows, total)
        self.progress.stop()
        self.progress.configure(mode="determinate", maximum=total, value=completed)
        percent = int(100 * completed / total)
        self.translated_rows.set(f"已翻译：{progress.translated_rows}")
        self.cache_rows.set(f"缓存/复用：{progress.cache_hits}")
        self.api_rows.set(f"API新翻译：{progress.api_translated_rows}")
        self.failed_rows.set(f"失败：{progress.failed_rows}")
        self.remaining_rows.set(f"剩余：{progress.remaining_rows}")
        self.eta.set(f"预计剩余：{format_eta(progress.eta_seconds)}")
        self.status.set(
            f"翻译进度 {completed}/{progress.total_rows} ({percent}%)，"
            f"API新翻译 {progress.api_translated_rows}，缓存/复用 {progress.cache_hits}，"
            f"失败 {progress.failed_rows}，剩余 {progress.remaining_rows}，"
            f"预计 {format_eta(progress.eta_seconds)}，API 批次 {progress.api_batches_done}/{progress.api_batches_total}"
        )

    def _handle_translation_event(self, event: object) -> None:
        if isinstance(event, TranslationProgress):
            self._show_translate_progress(event)
            return
        if isinstance(event, TranslationBatchStarted):
            self.current_batch_ids.clear()
            self.current_batch.set(f"当前批次：{event.batch_index}/{event.batch_total}，{event.item_count} 条")
            self.current_file_path = event.file_paths[0] if event.file_paths else ""
            self._append_log(
                f"请求第 {event.batch_index}/{event.batch_total} 批，本批 {event.item_count} 条，"
                f"来源文件 {truncate(', '.join(event.file_paths[:3]), 160)}"
            )
            return
        if isinstance(event, TranslationStarted):
            self.current_batch_ids.add(event.text_id)
            self.current_file_path = event.file_path
            self.current_text_id.set(f"text_id：{event.text_id}")
            self.current_file.set(f"文件：{truncate(event.file_path, 140)}")
            self.current_translation_status.set(f"状态：{event.status}")
            self.current_original.set(f"原文：{truncate(event.original, 180)}")
            if event.translation:
                self.current_translation.set(f"译文：{truncate(event.translation, 180)}")
            self._upsert_realtime_row(
                {
                    "id": event.text_id,
                    "status": event.status,
                    "source": event.source_type,
                    "file": event.file_path,
                    "original": event.original,
                    "translation": event.translation,
                    "note": "",
                    "batch": str(event.batch_index),
                }
            )
            return
        if isinstance(event, TranslationItemCompleted):
            self.current_translation.set(f"译文：{truncate(event.translation, 180)}")
            self._upsert_realtime_row(
                {
                    "id": event.text_id,
                    "status": event.status,
                    "source": event.source_type,
                    "file": event.file_path,
                    "original": event.original,
                    "translation": event.translation,
                    "note": "",
                    "batch": str(event.batch_index),
                }
            )
            return
        if isinstance(event, TranslationItemFailed):
            self._upsert_realtime_row(
                {
                    "id": event.text_id,
                    "status": "失败",
                    "source": event.source_type,
                    "file": event.file_path,
                    "original": event.original,
                    "translation": "",
                    "note": event.error,
                    "batch": str(event.batch_index),
                }
            )
            return
        if isinstance(event, TranslationBatchCompleted):
            self._append_log(
                f"第 {event.batch_index} 批完成：API耗时 {event.api_time:.2f}s，"
                f"新翻译 {event.translated_count}，失败 {event.failed_count}，"
                f"检查 {event.check_result}，写入缓存 {'是' if event.cached_written else '否'}"
            )

    def _load_existing_state(self, modpack_dir: Path) -> None:
        paths = project_paths(modpack_dir)
        self.output_dir.set(str(paths.output_dir))
        if paths.extracted_csv.exists():
            self.last_scan_translate_names = self._read_last_scan_translate_names(paths)
            records = read_extracted_csv(paths.extracted_csv)
            self._update_scan_stats(records)
            self._refresh_preview(records)
            self._log(f"已加载状态：{paths.extracted_jsonl}")
            self._mark_scan_dirty_if_needed(show_dialog=False)

    def _update_scan_stats(self, records: list, *, cache_hits: int = 0) -> None:
        total = len(records)
        ftb = sum(1 for record in records if record.source_type.startswith("ftbquests"))
        guides = sum(1 for record in records if record.source_type in {"jar_ae2guide", "jar_guides"})
        books = sum(1 for record in records if record.source_type in {"jar_patchouli", "jar_modonomicon"})
        item_names = count_lang_name_prefix(records, "item.")
        block_names = count_lang_name_prefix(records, "block.")
        entity_names = count_lang_name_prefix(records, "entity.")
        fluid_names = count_lang_name_prefix(records, "fluid.")
        translated = sum(1 for record in records if record.translation.strip())
        failed = sum(1 for record in records if record.note.lower().startswith("failed"))
        self.total_rows.set(f"总待翻译：{total}")
        self.ftb_rows.set(f"FTB Quests：{ftb}")
        self.guide_rows.set(f"GuideME/AE2：{guides}")
        self.book_rows.set(f"Patchouli/Modonomicon：{books}")
        self.item_name_rows.set(f"item.*：{item_names}")
        self.block_name_rows.set(f"block.*：{block_names}")
        self.entity_name_rows.set(f"entity.*：{entity_names}")
        self.fluid_name_rows.set(f"fluid.*：{fluid_names}")
        self.translated_rows.set(f"已翻译：{translated}")
        self.cache_rows.set(f"缓存/复用：{cache_hits}")
        self.api_rows.set("API新翻译：0")
        self.failed_rows.set(f"失败：{failed}")
        self.remaining_rows.set(f"剩余：{max(0, total - translated - failed)}")
        self.eta.set("预计剩余：--")
        self._check_name_scan_expectation(records)

    def _check_name_scan_expectation(self, records: list) -> None:
        if not self.translate_names.get():
            self.name_scan_warning.set("")
            return
        item_names = count_lang_name_prefix(records, "item.")
        block_names = count_lang_name_prefix(records, "block.")
        entity_names = count_lang_name_prefix(records, "entity.")
        if item_names + block_names + entity_names == 0:
            self.name_scan_warning.set(
                "已开启物品名翻译，但没有扫描到任何物品/方块/实体名称，请检查扫描器是否读取 mods/*.jar/assets/**/lang/en_us.json。"
            )
        else:
            self.name_scan_warning.set("")

    def _refresh_preview(self, records: list) -> None:
        self.realtime_rows = []
        for record in records[-self.realtime_limit :]:
            note = record.note or ("已翻译" if record.translation.strip() else "未翻译")
            self.realtime_rows.append(
                {
                    "id": record.id,
                    "status": note,
                    "source": record.source_type,
                    "file": record.file_path,
                    "original": record.original,
                    "translation": record.translation,
                    "note": record.note,
                    "batch": "",
                }
            )
        self._render_preview()

    def _upsert_realtime_row(self, row: dict[str, str]) -> None:
        existing_index = next((index for index, item in enumerate(self.realtime_rows) if item["id"] == row["id"]), None)
        if existing_index is not None:
            merged = {**self.realtime_rows.pop(existing_index), **row}
            self.realtime_rows.append(merged)
        else:
            self.realtime_rows.append(row)
        if len(self.realtime_rows) > self.realtime_limit:
            self.realtime_rows = self.realtime_rows[-self.realtime_limit :]
        self._schedule_render_preview()

    def _schedule_render_preview(self) -> None:
        if self.preview_render_scheduled:
            return
        self.preview_render_scheduled = True
        self.after(200, self._flush_preview_render)

    def _flush_preview_render(self) -> None:
        self.preview_render_scheduled = False
        self._render_preview()

    def _render_preview(self) -> None:
        selected = self.selected_text_id
        for item in self.preview.get_children():
            self.preview.delete(item)
        rows = self._filtered_realtime_rows()
        for row in rows:
            text_id = row["id"]
            self.preview.insert(
                "",
                "end",
                iid=text_id,
                values=(
                    truncate(text_id, 16),
                    truncate(row.get("status", ""), 28),
                    truncate(row.get("source", ""), 40),
                    truncate(row.get("file", ""), 80),
                    truncate(row.get("original", ""), 120),
                    truncate(row.get("translation", ""), 120),
                    truncate(row.get("note", ""), 80),
                ),
            )
        if rows:
            last_id = rows[-1]["id"]
            if selected and self.preview.exists(selected):
                self.preview.selection_set(selected)
                self.preview.see(selected)
            elif self.preview.exists(last_id):
                self.preview.see(last_id)

    def _filtered_realtime_rows(self) -> list[dict[str, str]]:
        mode = self.preview_filter.get()
        rows = list(self.realtime_rows)
        if mode == "全部":
            return rows
        if mode == "正在翻译":
            return [row for row in rows if row.get("status") in {"等待中", "请求 API 中", "检查中"}]
        if mode == "失败":
            return [row for row in rows if row.get("status") == "失败" or row.get("note", "").lower().startswith("failed")]
        if mode == "疑似问题":
            return [
                row
                for row in rows
                if any(token in (row.get("note", "") + row.get("status", "")).lower() for token in ("failed", "warning", "问题", "needs"))
            ]
        if mode == "当前文件":
            return [row for row in rows if row.get("file") == self.current_file_path]
        if mode == "当前批次":
            return [row for row in rows if row.get("id") in self.current_batch_ids]
        return [row for row in rows if row.get("status") in {"已翻译", "缓存复用", "review:ok", "需要重翻"} or row.get("translation")]

    def _on_preview_select(self, _event=None) -> None:
        selection = self.preview.selection()
        if not selection:
            return
        self.selected_text_id = selection[0]
        row = next((item for item in self.realtime_rows if item["id"] == self.selected_text_id), None)
        if row is None:
            return
        self._set_detail_text(self.original_detail, row.get("original", ""))
        self._set_detail_text(self.translation_detail, row.get("translation", ""))

    def _set_detail_text(self, widget: tk.Text, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)

    def _selected_row_id(self) -> str:
        selection = self.preview.selection()
        if not selection:
            raise RuntimeError("请先在表格中选择一条记录。")
        return selection[0]

    def _mark_selected_passed(self) -> None:
        try:
            text_id = self._selected_row_id()
            records = self._update_csv_record(text_id, note="review:ok")
            self._update_scan_stats(records)
            self._upsert_realtime_row({"id": text_id, "status": "review:ok", "note": "review:ok"})
        except Exception as error:  # noqa: BLE001 - GUI action should surface failure.
            messagebox.showerror("mc-han", str(error))

    def _mark_selected_needs_retranslate(self) -> None:
        try:
            text_id = self._selected_row_id()
            records = self._update_csv_record(text_id, translation="", note="needs_retranslate")
            self._update_scan_stats(records)
            self._upsert_realtime_row(
                {"id": text_id, "status": "需要重翻", "translation": "", "note": "needs_retranslate"}
            )
        except Exception as error:  # noqa: BLE001 - GUI action should surface failure.
            messagebox.showerror("mc-han", str(error))

    def _edit_selected_translation(self) -> None:
        try:
            text_id = self._selected_row_id()
        except Exception as error:  # noqa: BLE001 - GUI action should surface failure.
            messagebox.showerror("mc-han", str(error))
            return
        row = next((item for item in self.realtime_rows if item["id"] == text_id), None)
        initial = row.get("translation", "") if row else ""
        editor = tk.Toplevel(self)
        editor.title("编辑译文")
        editor.geometry("640x360")
        text = tk.Text(editor, wrap="word")
        text.pack(fill="both", expand=True, padx=10, pady=10)
        text.insert("1.0", initial)

        def save_edit() -> None:
            value = text.get("1.0", "end").strip()
            try:
                records = self._update_csv_record(text_id, translation=value, note="edited")
                self._update_scan_stats(records)
                self._upsert_realtime_row({"id": text_id, "status": "已编辑", "translation": value, "note": "edited"})
                self._set_detail_text(self.translation_detail, value)
            except Exception as error:  # noqa: BLE001 - GUI action should surface failure.
                messagebox.showerror("mc-han", str(error))
                return
            editor.destroy()

        ttk.Button(editor, text="保存", command=save_edit).pack(fill="x", padx=10, pady=(0, 10))

    def _retranslate_selected(self) -> None:
        try:
            text_id = self._selected_row_id()
        except Exception as error:  # noqa: BLE001 - GUI action should surface failure.
            messagebox.showerror("mc-han", str(error))
            return
        self._run_worker(lambda: self._retranslate_selected_worker(text_id))

    def _retranslate_selected_worker(self, text_id: str) -> None:
        modpack_dir = self._require_modpack()
        paths = project_paths(modpack_dir)
        self._update_csv_record(text_id, translation="", note="retranslate_requested")
        translator = self._create_translator()
        checkpoint = create_checkpoint(paths.extracted_csv, label=f"before-retranslate-{text_id}")
        self._log(f"已创建 checkpoint：{checkpoint.path}")
        records, translated_count, cache_hits = translate_csv(
            input_csv=paths.extracted_csv,
            output_csv=paths.extracted_csv,
            translator=translator,
            cache_path=paths.translation_cache_jsonl,
            sqlite_cache_path=paths.translations_sqlite,
            speed_mode=self._selected_speed_mode(),
            worker_count=1,
            max_batch_items=self._parse_optional_int(self.max_batch_items, "batch_size"),
            max_input_tokens=self._parse_optional_int(self.max_input_tokens, "token_limit"),
            max_output_tokens=self._parse_optional_int(self.max_output_tokens, "output_token"),
            force=True,
            target_ids={text_id},
            continue_on_error=True,
            name_translation_format=self._name_translation_format(),
            event_callback=self._emit_translation_event,
        )
        write_extracted_jsonl(records, paths.extracted_jsonl)
        self._emit_records(records, cache_hits=cache_hits)
        self._log(f"选中项重翻完成：新翻译 {translated_count} 条，缓存/复用 {cache_hits} 条")

    def _update_csv_record(
        self,
        text_id: str,
        *,
        translation: str | None = None,
        note: str | None = None,
    ) -> list:
        modpack_dir = self._require_modpack()
        paths = project_paths(modpack_dir)
        records = read_extracted_csv(paths.extracted_csv)
        updated = []
        found = False
        for record in records:
            if record.id != text_id:
                updated.append(record)
                continue
            found = True
            updated.append(
                record.__class__(
                    id=record.id,
                    source_type=record.source_type,
                    container=record.container,
                    file_path=record.file_path,
                    key_path=record.key_path,
                    original=record.original,
                    translation=record.translation if translation is None else translation,
                    note=record.note if note is None else note,
                )
            )
        if not found:
            raise RuntimeError(f"找不到记录：{text_id}")
        write_extracted_csv(updated, paths.extracted_csv)
        write_extracted_jsonl(updated, paths.extracted_jsonl)
        return updated

    def _csv_has_name_rows(self, csv_path: Path) -> bool:
        if not csv_path.exists():
            return False
        return any(record.source_type == "lang_name" for record in read_extracted_csv(csv_path))


def run_gui() -> int:
    app = McHanApp()
    app.mainloop()
    return 0


def truncate(text: str, limit: int) -> str:
    normalized = " ".join(str(text).split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 3)] + "..."


def format_eta(seconds: float | None) -> str:
    if seconds is None:
        return "--"
    seconds = max(0, int(seconds))
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}时{minutes:02d}分"
    if minutes:
        return f"{minutes}分{secs:02d}秒"
    return f"{secs}秒"


def count_lang_name_prefix(records: list, prefix: str) -> int:
    return sum(
        1
        for record in records
        if getattr(record, "source_type", "") == "lang_name" and getattr(record, "key_path", "").startswith(prefix)
    )
