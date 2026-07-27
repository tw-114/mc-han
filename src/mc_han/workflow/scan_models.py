from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Iterable

from mc_han.models import ExtractedText


class ScanCategoryId(str, Enum):
    MOD_LANGUAGE = "mod_language"
    FTB_QUESTS = "ftb_quests"
    PATCHOULI = "patchouli"
    MODONOMICON = "modonomicon"
    GUIDEME = "guideme"
    CONFIG_TEXT = "config_text"
    DISPLAY_NAMES = "display_names"
    OTHER_SUPPORTED = "other_supported"
    EXISTING_CHINESE = "existing_chinese"
    PROTECTED_SKIPPED = "protected_skipped"
    SAFETY_REJECTED = "safety_rejected"
    UNREADABLE_SOURCES = "unreadable_sources"


TRANSLATABLE_CATEGORY_ORDER = (
    ScanCategoryId.MOD_LANGUAGE,
    ScanCategoryId.FTB_QUESTS,
    ScanCategoryId.PATCHOULI,
    ScanCategoryId.MODONOMICON,
    ScanCategoryId.GUIDEME,
    ScanCategoryId.CONFIG_TEXT,
    ScanCategoryId.DISPLAY_NAMES,
    ScanCategoryId.OTHER_SUPPORTED,
)

INFORMATION_CATEGORY_ORDER = (
    ScanCategoryId.EXISTING_CHINESE,
    ScanCategoryId.PROTECTED_SKIPPED,
    ScanCategoryId.SAFETY_REJECTED,
    ScanCategoryId.UNREADABLE_SOURCES,
)

SCAN_CATEGORY_ORDER = TRANSLATABLE_CATEGORY_ORDER + INFORMATION_CATEGORY_ORDER

SOURCE_TYPE_TO_CATEGORY = {
    "jar_lang": ScanCategoryId.MOD_LANGUAGE,
    "resourcepack_lang": ScanCategoryId.MOD_LANGUAGE,
    "ftbquests_lang": ScanCategoryId.FTB_QUESTS,
    "ftbquests_snbt": ScanCategoryId.FTB_QUESTS,
    "jar_patchouli": ScanCategoryId.PATCHOULI,
    "jar_modonomicon": ScanCategoryId.MODONOMICON,
    "jar_ae2guide": ScanCategoryId.GUIDEME,
    "jar_guides": ScanCategoryId.GUIDEME,
    "kubejs_lang": ScanCategoryId.CONFIG_TEXT,
    "lang_name": ScanCategoryId.DISPLAY_NAMES,
}


def category_for_record(record: ExtractedText) -> ScanCategoryId:
    if not isinstance(record, ExtractedText):
        raise TypeError("record must be ExtractedText")
    return SOURCE_TYPE_TO_CATEGORY.get(
        record.source_type,
        ScanCategoryId.OTHER_SUPPORTED,
    )


@dataclass(frozen=True)
class ScanCategoryDefinition:
    category_id: ScanCategoryId
    title: str
    description: str
    translatable: bool
    recommended: bool


CATEGORY_DEFINITIONS = {
    ScanCategoryId.MOD_LANGUAGE: ScanCategoryDefinition(
        ScanCategoryId.MOD_LANGUAGE,
        "模组语言文件",
        "模组、资源包中的界面说明和提示文本",
        True,
        True,
    ),
    ScanCategoryId.FTB_QUESTS: ScanCategoryDefinition(
        ScanCategoryId.FTB_QUESTS,
        "FTB Quests",
        "任务书标题、说明和章节文本",
        True,
        True,
    ),
    ScanCategoryId.PATCHOULI: ScanCategoryDefinition(
        ScanCategoryId.PATCHOULI,
        "Patchouli 手册",
        "Patchouli 书本中的章节和正文",
        True,
        True,
    ),
    ScanCategoryId.MODONOMICON: ScanCategoryDefinition(
        ScanCategoryId.MODONOMICON,
        "Modonomicon 手册",
        "Modonomicon 书本中的章节和正文",
        True,
        True,
    ),
    ScanCategoryId.GUIDEME: ScanCategoryDefinition(
        ScanCategoryId.GUIDEME,
        "GuideME / AE2 指南",
        "GuideME、AE2 及兼容模组的指南页面",
        True,
        True,
    ),
    ScanCategoryId.CONFIG_TEXT: ScanCategoryDefinition(
        ScanCategoryId.CONFIG_TEXT,
        "配置和脚本文本",
        "KubeJS 等本地配置来源中的玩家可见文本",
        True,
        True,
    ),
    ScanCategoryId.DISPLAY_NAMES: ScanCategoryDefinition(
        ScanCategoryId.DISPLAY_NAMES,
        "物品与方块名称",
        "仅在明确开启名称翻译时出现，并保留英文原名",
        True,
        False,
    ),
    ScanCategoryId.OTHER_SUPPORTED: ScanCategoryDefinition(
        ScanCategoryId.OTHER_SUPPORTED,
        "其他受支持内容",
        "扫描器已识别、但暂不能更准确归类的内容",
        True,
        False,
    ),
    ScanCategoryId.EXISTING_CHINESE: ScanCategoryDefinition(
        ScanCategoryId.EXISTING_CHINESE,
        "已有中文资源",
        "检测阶段发现的 zh_cn 文件，仅供参考",
        False,
        False,
    ),
    ScanCategoryId.PROTECTED_SKIPPED: ScanCategoryDefinition(
        ScanCategoryId.PROTECTED_SKIPPED,
        "被保护或跳过的文本",
        "资源 ID、JSON key、格式内容和未开启的名称翻译不会进入待翻译清单",
        False,
        False,
    ),
    ScanCategoryId.SAFETY_REJECTED: ScanCategoryDefinition(
        ScanCategoryId.SAFETY_REJECTED,
        "安全原因拒绝的内容",
        "超出安全读取限制或压缩率异常的 JAR 条目",
        False,
        False,
    ),
    ScanCategoryId.UNREADABLE_SOURCES: ScanCategoryDefinition(
        ScanCategoryId.UNREADABLE_SOURCES,
        "损坏或无法读取的来源",
        "损坏、丢失或无法安全打开的 JAR",
        False,
        False,
    ),
}


@dataclass(frozen=True)
class ScanCategorySummary:
    category_id: ScanCategoryId
    title: str
    description: str
    translatable: bool
    default_selected: bool
    record_count: int
    file_count: int
    source_count: int
    selected: bool
    disabled_reason: str
    source_types: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.category_id, ScanCategoryId):
            raise TypeError("category_id must be ScanCategoryId")
        for field_name in ("title", "description", "disabled_reason"):
            if not isinstance(getattr(self, field_name), str):
                raise TypeError(f"{field_name} must be a string")
        for field_name in ("translatable", "default_selected", "selected"):
            if type(getattr(self, field_name)) is not bool:
                raise TypeError(f"{field_name} must be a bool")
        for field_name in ("record_count", "file_count", "source_count"):
            value = getattr(self, field_name)
            if type(value) is not int:
                raise TypeError(f"{field_name} must be an integer")
            if value < 0:
                raise ValueError(f"{field_name} must not be negative")

        source_types = _normalize_strings(self.source_types, "source_types")
        sources = _normalize_strings(self.sources, "sources")
        if self.source_count != len(sources):
            raise ValueError("source_count must match sources")
        if self.record_count > 0 and (self.file_count == 0 or self.source_count == 0):
            raise ValueError(
                "record_count requires positive file_count and source_count"
            )
        if not self.translatable:
            if self.record_count != 0:
                raise ValueError("information categories cannot contain records")
            if self.default_selected or self.selected:
                raise ValueError("information categories cannot be selected")
            if not self.disabled_reason:
                raise ValueError("information categories require disabled_reason")
        if self.selected and (self.record_count == 0 or not self.translatable):
            raise ValueError("selected categories require translatable records")
        object.__setattr__(self, "source_types", source_types)
        object.__setattr__(self, "sources", sources)

    def to_dict(self) -> dict[str, Any]:
        return {
            "category_id": self.category_id.value,
            "title": self.title,
            "description": self.description,
            "translatable": self.translatable,
            "default_selected": self.default_selected,
            "record_count": self.record_count,
            "file_count": self.file_count,
            "source_count": self.source_count,
            "selected": self.selected,
            "disabled_reason": self.disabled_reason,
            "source_types": list(self.source_types),
            "sources": list(self.sources),
        }


class ScanProgressStage(str, Enum):
    PREPARING = "preparing"
    SCANNING = "scanning"
    CLASSIFYING = "classifying"
    WRITING = "writing"
    COMPLETED = "completed"


@dataclass(frozen=True)
class ScanProgressEvent:
    stage: ScanProgressStage
    message: str
    current_source: str = ""
    discovered_records: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.stage, ScanProgressStage):
            raise TypeError("stage must be ScanProgressStage")
        if not isinstance(self.message, str) or not isinstance(
            self.current_source,
            str,
        ):
            raise TypeError("progress text fields must be strings")
        if type(self.discovered_records) is not int:
            raise TypeError("discovered_records must be an integer")
        if self.discovered_records < 0:
            raise ValueError("discovered_records must not be negative")


@dataclass(frozen=True)
class ScanDiagnostic:
    severity: str
    code: str
    message: str
    location: str = ""

    def __post_init__(self) -> None:
        if self.severity not in {"warning", "error", "info"}:
            raise ValueError("severity must be warning, error, or info")
        if not all(
            isinstance(value, str)
            for value in (self.code, self.message, self.location)
        ):
            raise TypeError("diagnostic fields must be strings")


@dataclass(frozen=True)
class ScanClassificationResult:
    categories: tuple[ScanCategorySummary, ...]
    diagnostics: tuple[ScanDiagnostic, ...]
    total_translatable_records: int
    total_file_count: int
    total_source_count: int
    scan_duration: float
    output_csv: str
    report_path: str

    def __post_init__(self) -> None:
        categories = tuple(self.categories)
        diagnostics = tuple(self.diagnostics)
        if not all(isinstance(item, ScanCategorySummary) for item in categories):
            raise TypeError("categories must contain ScanCategorySummary values")
        if not all(isinstance(item, ScanDiagnostic) for item in diagnostics):
            raise TypeError("diagnostics must contain ScanDiagnostic values")
        category_ids = tuple(item.category_id for item in categories)
        if len(set(category_ids)) != len(category_ids):
            raise ValueError("categories must have unique category_id values")
        order = {category_id: index for index, category_id in enumerate(SCAN_CATEGORY_ORDER)}
        categories = tuple(
            sorted(
                categories,
                key=lambda item: order.get(item.category_id, len(order)),
            )
        )
        for field_name in (
            "total_translatable_records",
            "total_file_count",
            "total_source_count",
        ):
            value = getattr(self, field_name)
            if type(value) is not int:
                raise TypeError(f"{field_name} must be an integer")
            if value < 0:
                raise ValueError(f"{field_name} must not be negative")
        if isinstance(self.scan_duration, bool) or not isinstance(
            self.scan_duration,
            (int, float),
        ):
            raise TypeError("scan_duration must be numeric")
        if self.scan_duration < 0:
            raise ValueError("scan_duration must not be negative")
        if not isinstance(self.output_csv, str) or not isinstance(
            self.report_path,
            str,
        ):
            raise TypeError("output paths must be strings")
        calculated_total = sum(
            item.record_count for item in categories if item.translatable
        )
        if calculated_total != self.total_translatable_records:
            raise ValueError("total_translatable_records must match categories")
        severity_order = {"error": 0, "warning": 1, "info": 2}
        diagnostics = tuple(
            sorted(
                diagnostics,
                key=lambda item: (
                    severity_order[item.severity],
                    item.code,
                    item.location.casefold(),
                    item.location,
                    item.message,
                ),
            )
        )
        object.__setattr__(self, "categories", categories)
        object.__setattr__(self, "diagnostics", diagnostics)

    @property
    def warning_count(self) -> int:
        return sum(
            item.severity in {"warning", "error"} for item in self.diagnostics
        )


@dataclass(frozen=True)
class ScanSelectionState:
    result: ScanClassificationResult
    selected_category_ids: frozenset[ScanCategoryId]

    def __post_init__(self) -> None:
        if not isinstance(self.result, ScanClassificationResult):
            raise TypeError("result must be ScanClassificationResult")
        selected = frozenset(self.selected_category_ids)
        valid_ids = {
            item.category_id
            for item in self.result.categories
            if item.translatable and item.record_count > 0
        }
        if not selected.issubset(valid_ids):
            raise ValueError("selected_category_ids contains a disabled category")
        object.__setattr__(self, "selected_category_ids", selected)

    @classmethod
    def from_result(cls, result: ScanClassificationResult) -> "ScanSelectionState":
        return cls(
            result=result,
            selected_category_ids=frozenset(
                item.category_id
                for item in result.categories
                if item.selected
            ),
        )

    @property
    def selected_record_count(self) -> int:
        return sum(
            item.record_count
            for item in self.result.categories
            if item.category_id in self.selected_category_ids
        )

    @property
    def total_record_count(self) -> int:
        return self.result.total_translatable_records

    @property
    def categories(self) -> tuple[ScanCategorySummary, ...]:
        return tuple(
            replace(
                item,
                selected=item.category_id in self.selected_category_ids,
            )
            for item in self.result.categories
        )

    def set_selected(
        self,
        category_id: ScanCategoryId,
        selected: bool,
    ) -> "ScanSelectionState":
        if type(selected) is not bool:
            raise TypeError("selected must be a bool")
        available = {
            item.category_id
            for item in self.result.categories
            if item.translatable and item.record_count > 0
        }
        if category_id not in available:
            return self
        updated = set(self.selected_category_ids)
        if selected:
            updated.add(category_id)
        else:
            updated.discard(category_id)
        return replace(self, selected_category_ids=frozenset(updated))

    def select_all(self) -> "ScanSelectionState":
        return replace(
            self,
            selected_category_ids=frozenset(
                item.category_id
                for item in self.result.categories
                if item.translatable and item.record_count > 0
            ),
        )

    def clear(self) -> "ScanSelectionState":
        return replace(self, selected_category_ids=frozenset())

    def restore_defaults(self) -> "ScanSelectionState":
        return replace(
            self,
            selected_category_ids=frozenset(
                item.category_id
                for item in self.result.categories
                if item.default_selected and item.record_count > 0
            ),
        )

    def is_record_selected(self, record: ExtractedText) -> bool:
        category_id = category_for_record(record)
        definition = CATEGORY_DEFINITIONS[category_id]
        return (
            definition.translatable
            and category_id in self.selected_category_ids
        )

    def selected_records(
        self,
        records: Iterable[ExtractedText],
    ) -> tuple[ExtractedText, ...]:
        return tuple(
            record
            for record in records
            if self.is_record_selected(record)
        )


def _normalize_strings(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    normalized = tuple(values)
    if not all(isinstance(item, str) for item in normalized):
        raise TypeError(f"{field_name} must contain strings")
    if any(not item for item in normalized):
        raise ValueError(f"{field_name} must not contain empty strings")
    return tuple(sorted(set(normalized), key=lambda value: (value.casefold(), value)))
