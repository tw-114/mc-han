from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from mc_han.usage.models import TranslationUsageSummary
from mc_han.workflow.scan_models import ScanCategoryId


class TrialSampleStatus(str, Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"


class TrialProgressStage(str, Enum):
    PREPARING = "preparing"
    TRANSLATING = "translating"
    COMPLETED = "completed"


@dataclass(frozen=True)
class TrialSampleResult:
    text_id: str
    original: str
    translation: str
    category_id: ScanCategoryId
    category_title: str
    source: str
    status: TrialSampleStatus = TrialSampleStatus.PENDING
    from_cache: bool = False

    def __post_init__(self) -> None:
        for field_name in (
            "text_id",
            "original",
            "translation",
            "category_title",
            "source",
        ):
            if not isinstance(getattr(self, field_name), str):
                raise TypeError(f"{field_name} must be a string")
        if not self.text_id or not self.original:
            raise ValueError("text_id and original must not be empty")
        if not isinstance(self.category_id, ScanCategoryId):
            raise TypeError("category_id must be a ScanCategoryId")
        if not isinstance(self.status, TrialSampleStatus):
            raise TypeError("status must be a TrialSampleStatus")
        if type(self.from_cache) is not bool:
            raise TypeError("from_cache must be a bool")
        if self.status is TrialSampleStatus.SUCCESS and not self.translation:
            raise ValueError("successful trial samples require a translation")
        if self.status is not TrialSampleStatus.SUCCESS and self.from_cache:
            raise ValueError("only successful trial samples may be cached")


@dataclass(frozen=True)
class TrialProgressEvent:
    stage: TrialProgressStage
    message: str
    completed: int
    total: int

    def __post_init__(self) -> None:
        if not isinstance(self.stage, TrialProgressStage):
            raise TypeError("stage must be a TrialProgressStage")
        if not isinstance(self.message, str):
            raise TypeError("message must be a string")
        for field_name in ("completed", "total"):
            value = getattr(self, field_name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        if self.completed > self.total:
            raise ValueError("completed must not exceed total")


@dataclass(frozen=True)
class TrialTranslationResult:
    samples: tuple[TrialSampleResult, ...]
    usage: TranslationUsageSummary
    elapsed_seconds: float
    task_id: str

    def __post_init__(self) -> None:
        samples = tuple(self.samples)
        if not all(isinstance(item, TrialSampleResult) for item in samples):
            raise TypeError("samples must contain TrialSampleResult values")
        if not isinstance(self.usage, TranslationUsageSummary):
            raise TypeError("usage must be a TranslationUsageSummary")
        if (
            isinstance(self.elapsed_seconds, bool)
            or not isinstance(self.elapsed_seconds, (int, float))
            or self.elapsed_seconds < 0
        ):
            raise ValueError("elapsed_seconds must be non-negative")
        if not isinstance(self.task_id, str) or not self.task_id:
            raise ValueError("task_id must not be empty")
        object.__setattr__(self, "samples", samples)

    @property
    def failed_ids(self) -> frozenset[str]:
        return frozenset(
            item.text_id
            for item in self.samples
            if item.status is TrialSampleStatus.FAILED
        )

    @property
    def successful_count(self) -> int:
        return sum(
            item.status is TrialSampleStatus.SUCCESS for item in self.samples
        )

    @property
    def failed_count(self) -> int:
        return len(self.failed_ids)

    @property
    def display_cost(self) -> tuple[Decimal | None, str, str]:
        if (
            self.usage.reported_cost_complete
            and self.usage.reported_cost_total is not None
        ):
            return (
                self.usage.reported_cost_total,
                self.usage.reported_cost_currency,
                "服务商报告",
            )
        if self.usage.estimated_cost is not None:
            return (
                self.usage.estimated_cost,
                self.usage.estimated_cost_currency,
                "估算",
            )
        return None, "", "暂无法确定"
