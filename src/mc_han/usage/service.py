from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from decimal import Decimal
from typing import Callable, Iterable

from mc_han.workflow.scan_models import SCAN_CATEGORY_ORDER

from .ledger import UsageLedger
from .models import TranslationUsageSummary, UsageBreakdown, UsageOutcome


class UsageQueryService:
    def __init__(self, ledger: UsageLedger):
        self.ledger = ledger

    def task_summary(self, task_id: str) -> TranslationUsageSummary:
        return self._summarize(
            self.ledger.attempt_rows(task_id=task_id),
            self.ledger.task_stats_rows(task_id=task_id),
        )

    def project_summary(self) -> TranslationUsageSummary:
        return self._summarize(
            self.ledger.attempt_rows(),
            self.ledger.task_stats_rows(),
        )

    def breakdown_by_provider(self) -> tuple[UsageBreakdown, ...]:
        return self._breakdown("provider", lambda row: str(row["provider"]))

    def breakdown_by_model(self) -> tuple[UsageBreakdown, ...]:
        return self._breakdown("model", lambda row: str(row["model"]))

    def breakdown_by_outcome(self) -> tuple[UsageBreakdown, ...]:
        return self._breakdown("outcome", lambda row: str(row["outcome"]))

    def breakdown_by_thinking_mode(self) -> tuple[UsageBreakdown, ...]:
        return self._breakdown(
            "thinking_mode",
            lambda row: str(row["thinking_mode"]) or "unknown",
        )

    def breakdown_by_task(self) -> tuple[UsageBreakdown, ...]:
        return self._breakdown("task", lambda row: str(row["task_id"]))

    def breakdown_by_category(self) -> tuple[UsageBreakdown, ...]:
        attempts = {
            str(row["event_id"]): row for row in self.ledger.attempt_rows()
        }
        grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
        category_counts: dict[tuple[str, str], int] = {}
        event_category_count: dict[str, int] = defaultdict(int)
        for category_row in self.ledger.category_rows():
            event_id = str(category_row["event_id"])
            category = str(category_row["category_id"])
            event_category_count[event_id] += 1
            category_counts[(event_id, category)] = int(category_row["item_count"])
            if event_id in attempts:
                grouped[category].append(attempts[event_id])

        results: list[UsageBreakdown] = []
        category_order = {
            category_id.value: index
            for index, category_id in enumerate(SCAN_CATEGORY_ORDER)
        }
        ordered_categories = sorted(
            grouped,
            key=lambda category: (
                category_order.get(category, len(category_order)),
                category,
            ),
        )
        for category in ordered_categories:
            rows = grouped[category]
            translated_override = sum(
                category_counts[(str(row["event_id"]), category)]
                for row in rows
                if row["outcome"] == UsageOutcome.SUCCESS.value
            )
            mixed_event_ids = {
                str(row["event_id"])
                for row in rows
                if event_category_count[str(row["event_id"])] > 1
            }
            summary = self._summarize(
                rows,
                (),
                translated_items_override=translated_override,
                token_excluded_event_ids=mixed_event_ids,
            )
            results.append(UsageBreakdown("category", category, summary))
        return tuple(results)

    def retry_summary(self) -> dict[str, int]:
        rows = self.ledger.attempt_rows()
        return {
            "retry_count": sum(int(row["attempt_number"]) > 1 for row in rows),
            "retried_batch_count": len(
                {
                    (str(row["task_id"]), str(row["batch_id"]))
                    for row in rows
                    if int(row["attempt_number"]) > 1
                }
            ),
        }

    def latency_summary(self) -> dict[str, float | int | None]:
        summary = self.project_summary()
        return {
            "request_count": summary.api_attempts,
            "total_latency_ms": summary.total_latency_ms,
            "average_latency_ms": summary.average_latency_ms,
            "p50_latency_ms": summary.p50_latency_ms,
            "p95_latency_ms": summary.p95_latency_ms,
        }

    def _breakdown(
        self,
        dimension: str,
        key_for: Callable[[sqlite3.Row], str],
    ) -> tuple[UsageBreakdown, ...]:
        grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for row in self.ledger.attempt_rows():
            grouped[key_for(row)].append(row)
        return tuple(
            UsageBreakdown(dimension, key, self._summarize(rows, ()))
            for key, rows in sorted(grouped.items())
        )

    def _summarize(
        self,
        attempts: Iterable[sqlite3.Row],
        task_stats: Iterable[sqlite3.Row],
        *,
        translated_items_override: int | None = None,
        token_excluded_event_ids: set[str] | None = None,
    ) -> TranslationUsageSummary:
        rows = list(attempts)
        stats = list(task_stats)
        excluded = token_excluded_event_ids or set()
        successful = sum(
            row["outcome"] == UsageOutcome.SUCCESS.value for row in rows
        )
        translated_items = (
            translated_items_override
            if translated_items_override is not None
            else sum(
                int(row["item_count"])
                for row in rows
                if row["outcome"] == UsageOutcome.SUCCESS.value
            )
        )
        incomplete_usage_count = sum(
            _usage_is_incomplete(row, excluded) for row in rows
        )
        latencies = sorted(int(row["latency_ms"]) for row in rows)
        reported_currencies = {
            str(row["currency"])
            for row in rows
            if str(row["currency"])
            and row["provider_reported_cost"] is not None
        }
        estimated_currencies = {
            str(row["currency"])
            for row in rows
            if str(row["currency"]) and row["estimated_cost"] is not None
        }
        reported_currency = (
            next(iter(reported_currencies))
            if len(reported_currencies) == 1
            else ""
        )
        estimated_currency = (
            next(iter(estimated_currencies))
            if len(estimated_currencies) == 1
            else ""
        )
        missing_reported_cost_count = sum(
            row["provider_reported_cost"] is None for row in rows
        )
        reported = _sum_decimal(rows, "provider_reported_cost")
        estimated = _sum_complete_decimal(rows, "estimated_cost")
        if excluded:
            reported = None
            estimated = None
            missing_reported_cost_count = len(rows)
        if len(reported_currencies) > 1:
            reported = None
        if len(estimated_currencies) > 1:
            estimated = None
        reported_cost_complete = (
            missing_reported_cost_count == 0
            and len(reported_currencies) <= 1
        )
        return TranslationUsageSummary(
            api_attempts=len(rows),
            successful_attempts=successful,
            failed_attempts=len(rows) - successful,
            translated_items=translated_items,
            reused_items=sum(int(row["reused_items"]) for row in stats),
            avoided_api_items=sum(int(row["avoided_api_items"]) for row in stats),
            local_validation_failed_items=sum(
                int(row["item_count"])
                for row in rows
                if row["outcome"]
                == UsageOutcome.LOCAL_VALIDATION_FAILED.value
            ),
            remaining_items=(
                int(max(stats, key=lambda row: str(row["updated_at"]))["remaining_items"])
                if stats
                else 0
            ),
            input_tokens=_sum_optional_counts(rows, "input_tokens", excluded),
            cached_input_tokens=_sum_optional_counts(
                rows,
                "cached_input_tokens",
                excluded,
            ),
            output_tokens=_sum_optional_counts(rows, "output_tokens", excluded),
            reasoning_tokens=_sum_optional_counts(
                rows,
                "reasoning_tokens",
                excluded,
            ),
            total_tokens=_sum_optional_counts(rows, "total_tokens", excluded),
            total_latency_ms=sum(latencies),
            average_latency_ms=(
                sum(latencies) / len(latencies) if latencies else None
            ),
            p50_latency_ms=_percentile(latencies, 0.50),
            p95_latency_ms=_percentile(latencies, 0.95),
            retry_count=sum(int(row["attempt_number"]) > 1 for row in rows),
            reported_cost_total=reported,
            reported_cost_complete=reported_cost_complete,
            missing_reported_cost_count=missing_reported_cost_count,
            reported_cost_currency=reported_currency,
            estimated_cost=estimated,
            estimated_cost_currency=estimated_currency,
            unmatched_pricing_count=sum(
                not str(row["pricing_profile_id"]) for row in rows
            ),
            incomplete_usage_count=incomplete_usage_count,
        )


def _sum_optional_counts(
    rows: list[sqlite3.Row],
    field_name: str,
    excluded_event_ids: set[str],
) -> int | None:
    if not rows:
        return 0
    if any(
        row[field_name] is None or str(row["event_id"]) in excluded_event_ids
        for row in rows
    ):
        return None
    return sum(int(row[field_name]) for row in rows)


def _usage_is_incomplete(
    row: sqlite3.Row,
    excluded_event_ids: set[str],
) -> bool:
    if str(row["event_id"]) in excluded_event_ids:
        return True
    if any(
        row[field_name] is None
        for field_name in ("input_tokens", "output_tokens", "total_tokens")
    ):
        return True
    diagnostics = json.loads(str(row["usage_diagnostics_json"]))
    if not isinstance(diagnostics, list) or not all(
        isinstance(item, str) for item in diagnostics
    ):
        raise ValueError("usage diagnostics must be a list of strings")
    return bool(diagnostics)


def _sum_decimal(
    rows: list[sqlite3.Row],
    field_name: str,
) -> Decimal | None:
    values = [
        Decimal(str(row[field_name]))
        for row in rows
        if row[field_name] is not None
    ]
    return sum(values, Decimal(0)) if values else None


def _sum_complete_decimal(
    rows: list[sqlite3.Row],
    field_name: str,
) -> Decimal | None:
    if not rows or any(row[field_name] is None for row in rows):
        return None
    return sum(
        (Decimal(str(row[field_name])) for row in rows),
        Decimal(0),
    )


def _percentile(values: list[int], quantile: float) -> float | None:
    if not values:
        return None
    index = max(0, math.ceil(quantile * len(values)) - 1)
    return float(values[index])
