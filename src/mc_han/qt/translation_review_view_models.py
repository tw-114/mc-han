from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from mc_han.models import ExtractedText
from mc_han.quality.checks import CheckIssue
from mc_han.review import row_location
from mc_han.workflow.scan_models import (
    CATEGORY_DEFINITIONS,
    category_for_record,
)


class ReviewFilterId(str, Enum):
    ISSUES = "issues"
    ALL = "all"
    UNTRANSLATED = "untranslated"
    FAILED = "failed"
    UNREVIEWED = "unreviewed"
    REVIEWED = "reviewed"
    SKIPPED = "skipped"


class ReviewRowStatus(str, Enum):
    UNTRANSLATED = "untranslated"
    FAILED = "failed"
    UNREVIEWED = "unreviewed"
    REVIEWED = "reviewed"
    NEEDS_RETRANSLATE = "needs_retranslate"
    SKIPPED = "skipped"


STATUS_LABELS = {
    ReviewRowStatus.UNTRANSLATED: "未翻译",
    ReviewRowStatus.FAILED: "失败",
    ReviewRowStatus.UNREVIEWED: "未审核",
    ReviewRowStatus.REVIEWED: "已审核",
    ReviewRowStatus.NEEDS_RETRANSLATE: "需要重译",
    ReviewRowStatus.SKIPPED: "已跳过",
}


@dataclass(frozen=True)
class ReviewRowViewModel:
    text_id: str
    original: str
    translation: str
    source: str
    location: str
    category: str
    status: ReviewRowStatus
    status_text: str
    issues: tuple[str, ...]
    has_error: bool
    has_warning: bool

    @property
    def search_text(self) -> str:
        return "\n".join(
            (
                self.text_id,
                self.original,
                self.translation,
                self.source,
                self.location,
                self.category,
                self.status_text,
            )
        ).casefold()


@dataclass(frozen=True)
class TranslationReviewPageViewModel:
    rows: tuple[ReviewRowViewModel, ...]
    total_count: int
    translated_count: int
    reviewed_count: int
    skipped_count: int
    error_count: int
    warning_count: int
    passed_count: int
    needs_confirmation_count: int
    unresolved_count: int
    cost_text: str

    @classmethod
    def from_data(
        cls,
        records: tuple[ExtractedText, ...],
        issues: tuple[CheckIssue, ...],
        *,
        cost_amount: Decimal | None = None,
        cost_currency: str = "",
    ) -> "TranslationReviewPageViewModel":
        issues_by_location: dict[str, list[CheckIssue]] = defaultdict(list)
        for issue in issues:
            issues_by_location[issue.location].append(issue)
        rows = tuple(
            _row_view_model(
                record,
                tuple(issues_by_location.get(row_location(record), ())),
            )
            for record in records
        )
        return cls(
            rows=rows,
            total_count=len(rows),
            translated_count=sum(
                bool(row.translation.strip()) for row in rows
            ),
            reviewed_count=sum(
                row.status is ReviewRowStatus.REVIEWED for row in rows
            ),
            skipped_count=sum(
                row.status is ReviewRowStatus.SKIPPED for row in rows
            ),
            error_count=sum(
                issue.severity == "error" for issue in issues
            ),
            warning_count=sum(
                issue.severity == "warning" for issue in issues
            ),
            passed_count=sum(
                bool(row.translation.strip())
                and not row.has_error
                and not row.has_warning
                and row.status is not ReviewRowStatus.SKIPPED
                for row in rows
            ),
            needs_confirmation_count=sum(
                row.has_warning
                and row.status is ReviewRowStatus.UNREVIEWED
                for row in rows
            ),
            unresolved_count=sum(
                (
                    row.has_error
                    or row.status in {
                        ReviewRowStatus.UNTRANSLATED,
                        ReviewRowStatus.FAILED,
                        ReviewRowStatus.NEEDS_RETRANSLATE,
                    }
                )
                and row.status is not ReviewRowStatus.REVIEWED
                for row in rows
            ),
            cost_text=(
                f"{cost_currency or 'USD'} {cost_amount:.4f}"
                if cost_amount is not None
                else "暂无费用记录"
            ),
        )

    def filtered_rows(
        self,
        query: str,
        filter_id: ReviewFilterId,
    ) -> tuple[ReviewRowViewModel, ...]:
        if not isinstance(query, str):
            raise TypeError("query must be a string")
        if not isinstance(filter_id, ReviewFilterId):
            raise TypeError("filter_id must be ReviewFilterId")
        normalized_query = query.strip().casefold()
        return tuple(
            row
            for row in self.rows
            if (
                not normalized_query
                or normalized_query in row.search_text
            )
            and _matches_filter(row, filter_id)
        )


def _row_view_model(
    record: ExtractedText,
    issues: tuple[CheckIssue, ...],
) -> ReviewRowViewModel:
    status = _record_status(record)
    category = CATEGORY_DEFINITIONS[category_for_record(record)].title
    return ReviewRowViewModel(
        text_id=record.id,
        original=record.original,
        translation=record.translation,
        source=record.container or "整合包文件",
        location=f"{record.file_path} :: {record.key_path}",
        category=category,
        status=status,
        status_text=STATUS_LABELS[status],
        issues=tuple(
            f"{issue.code}: {issue.message}" for issue in issues
        ),
        has_error=any(issue.severity == "error" for issue in issues),
        has_warning=any(issue.severity == "warning" for issue in issues),
    )


def _record_status(record: ExtractedText) -> ReviewRowStatus:
    note = record.note.strip().casefold()
    review_status = record.review_status.strip().casefold()
    if record.skip_status.strip():
        return ReviewRowStatus.SKIPPED
    if note.startswith("failed"):
        return ReviewRowStatus.FAILED
    if (
        review_status == "needs_retranslate"
        or note == "needs_retranslate"
    ):
        return ReviewRowStatus.NEEDS_RETRANSLATE
    if review_status == "approved" or note == "review:ok":
        return ReviewRowStatus.REVIEWED
    if not record.translation.strip():
        return ReviewRowStatus.UNTRANSLATED
    return ReviewRowStatus.UNREVIEWED


def _matches_filter(
    row: ReviewRowViewModel,
    filter_id: ReviewFilterId,
) -> bool:
    if filter_id is ReviewFilterId.ALL:
        return True
    if filter_id is ReviewFilterId.ISSUES:
        return (
            row.status not in {
                ReviewRowStatus.REVIEWED,
                ReviewRowStatus.SKIPPED,
            }
            and (
                row.has_error
                or row.has_warning
                or row.status in {
                    ReviewRowStatus.UNTRANSLATED,
                    ReviewRowStatus.FAILED,
                    ReviewRowStatus.NEEDS_RETRANSLATE,
                }
            )
        )
    if filter_id is ReviewFilterId.UNTRANSLATED:
        return (
            not row.translation.strip()
            and row.status is not ReviewRowStatus.SKIPPED
        )
    if filter_id is ReviewFilterId.FAILED:
        return row.status is ReviewRowStatus.FAILED
    if filter_id is ReviewFilterId.UNREVIEWED:
        return row.status in {
            ReviewRowStatus.UNTRANSLATED,
            ReviewRowStatus.FAILED,
            ReviewRowStatus.UNREVIEWED,
            ReviewRowStatus.NEEDS_RETRANSLATE,
        }
    if filter_id is ReviewFilterId.REVIEWED:
        return row.status is ReviewRowStatus.REVIEWED
    return row.status is ReviewRowStatus.SKIPPED
