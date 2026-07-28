from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from mc_han.usage.ledger import SCHEMA_VERSION, UsageLedger, UsageSchemaError
from mc_han.usage.models import (
    ApiAttemptUsage,
    TokenUsage,
    UsageCategoryCount,
    UsageOutcome,
)
from mc_han.usage.service import UsageQueryService
from mc_han.workflow.scan_models import ScanCategoryId


def test_ledger_persists_attempts_and_prevents_duplicate_event_ids(
    tmp_path: Path,
):
    path = tmp_path / "usage.sqlite3"
    event = make_event(event_id="event-1")
    with UsageLedger(path) as ledger:
        assert ledger.record_attempt(event)
        assert not ledger.record_attempt(event)

    with UsageLedger(path) as reopened:
        assert reopened.schema_version() == SCHEMA_VERSION
        summary = UsageQueryService(reopened).project_summary()

    assert summary.api_attempts == 1
    assert summary.successful_attempts == 1
    assert summary.input_tokens == 100
    assert summary.total_tokens == 150


def test_each_attempt_is_stored_separately_and_retries_are_counted(
    tmp_path: Path,
):
    with UsageLedger(tmp_path / "usage.sqlite3") as ledger:
        ledger.record_attempt(
            make_event(
                event_id="first",
                attempt_number=1,
                outcome=UsageOutcome.RATE_LIMITED,
            )
        )
        ledger.record_attempt(
            make_event(event_id="second", attempt_number=2)
        )
        service = UsageQueryService(ledger)

        summary = service.project_summary()
        retry = service.retry_summary()

    assert summary.api_attempts == 2
    assert summary.successful_attempts == 1
    assert summary.failed_attempts == 1
    assert summary.retry_count == 1
    assert retry == {"retry_count": 1, "retried_batch_count": 1}


def test_ledger_transaction_failure_preserves_existing_rows(tmp_path: Path):
    path = tmp_path / "usage.sqlite3"
    ledger = UsageLedger(path)
    ledger.record_attempt(make_event(event_id="kept"))
    ledger._connection.execute(  # noqa: SLF001 - controlled fault injection.
        """
        CREATE TRIGGER reject_new_usage
        BEFORE INSERT ON api_attempts
        WHEN NEW.event_id = 'rejected'
        BEGIN
            SELECT RAISE(ABORT, 'simulated write failure');
        END
        """
    )
    ledger._connection.commit()  # noqa: SLF001

    with pytest.raises(sqlite3.IntegrityError):
        ledger.record_attempt(make_event(event_id="rejected"))

    assert [row["event_id"] for row in ledger.attempt_rows()] == ["kept"]
    ledger.close()


def test_older_schema_is_rejected_without_changing_version(tmp_path: Path):
    path = tmp_path / "usage.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE schema_version(version INTEGER NOT NULL)")
    connection.execute("INSERT INTO schema_version(version) VALUES (0)")
    connection.commit()
    connection.close()

    with pytest.raises(UsageSchemaError) as caught:
        UsageLedger(path)

    connection = sqlite3.connect(path)
    version = connection.execute(
        "SELECT version FROM schema_version"
    ).fetchone()[0]
    connection.close()
    assert caught.value.code == "usage_schema_older"
    assert version == 0


def test_newer_schema_is_rejected_without_changing_version(tmp_path: Path):
    path = tmp_path / "usage.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE schema_version(version INTEGER NOT NULL)")
    connection.execute(
        "INSERT INTO schema_version(version) VALUES (?)",
        (SCHEMA_VERSION + 1,),
    )
    connection.commit()
    connection.close()

    with pytest.raises(UsageSchemaError) as caught:
        UsageLedger(path)

    connection = sqlite3.connect(path)
    version = connection.execute(
        "SELECT version FROM schema_version"
    ).fetchone()[0]
    connection.close()
    assert caught.value.code == "usage_schema_newer"
    assert version == SCHEMA_VERSION + 1


def test_current_schema_initialization_is_idempotent(tmp_path: Path):
    path = tmp_path / "usage.sqlite3"
    with UsageLedger(path) as ledger:
        assert ledger.schema_version() == SCHEMA_VERSION
    with UsageLedger(path) as ledger:
        assert ledger.schema_version() == SCHEMA_VERSION


def test_schema_has_required_query_indexes(tmp_path: Path):
    path = tmp_path / "usage.sqlite3"
    with UsageLedger(path):
        pass
    connection = sqlite3.connect(path)
    indexes = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        )
    }
    connection.close()

    assert {
        "idx_usage_task",
        "idx_usage_provider_model",
        "idx_usage_started",
        "idx_usage_category",
    }.issubset(indexes)


def test_summary_tracks_unknown_usage_without_treating_it_as_zero(
    tmp_path: Path,
):
    with UsageLedger(tmp_path / "usage.sqlite3") as ledger:
        ledger.record_attempt(make_event(event_id="known"))
        ledger.record_attempt(
            replace(
                make_event(event_id="unknown"),
                tokens=TokenUsage(),
                estimated_cost=None,
                pricing_profile_id="",
            )
        )
        summary = UsageQueryService(ledger).project_summary()

    assert summary.input_tokens is None
    assert summary.output_tokens is None
    assert summary.incomplete_usage_count == 1
    assert summary.unmatched_pricing_count == 1


def test_missing_total_tokens_marks_attempt_usage_incomplete(tmp_path: Path):
    without_total = replace(
        make_event(event_id="missing-total"),
        tokens=TokenUsage(
            input_tokens=100,
            output_tokens=50,
            cached_input_tokens=20,
            uncached_input_tokens=80,
        ),
    )
    with UsageLedger(tmp_path / "usage.sqlite3") as ledger:
        ledger.record_attempt(without_total)
        summary = UsageQueryService(ledger).project_summary()

    assert summary.total_tokens is None
    assert summary.incomplete_usage_count == 1


def test_latency_percentiles_and_breakdowns(tmp_path: Path):
    with UsageLedger(tmp_path / "usage.sqlite3") as ledger:
        for index, latency in enumerate((10, 20, 30, 40, 100), start=1):
            ledger.record_attempt(
                replace(
                    make_event(event_id=f"event-{index}"),
                    latency_ms=latency,
                    provider="provider-a" if index < 5 else "provider-b",
                    model="model-a" if index < 5 else "model-b",
                )
            )
        service = UsageQueryService(ledger)
        summary = service.project_summary()

        providers = service.breakdown_by_provider()
        models = service.breakdown_by_model()

    assert summary.p50_latency_ms == 30
    assert summary.p95_latency_ms == 100
    assert summary.average_latency_ms == 40
    assert [item.key for item in providers] == ["provider-a", "provider-b"]
    assert [item.key for item in models] == ["model-a", "model-b"]


def test_model_breakdown_preserves_safe_provider_namespace(tmp_path: Path):
    with UsageLedger(tmp_path / "usage.sqlite3") as ledger:
        ledger.record_attempt(
            replace(
                make_event(event_id="namespaced-model"),
                model="vendor/model-v1",
            )
        )
        models = UsageQueryService(ledger).breakdown_by_model()

    assert [item.key for item in models] == ["vendor/model-v1"]


def test_outcome_thinking_and_task_breakdowns_are_available(tmp_path: Path):
    with UsageLedger(tmp_path / "usage.sqlite3") as ledger:
        ledger.record_attempt(make_event(event_id="first"))
        ledger.record_attempt(
            replace(
                make_event(event_id="second"),
                task_id="task-2",
                batch_id="batch-2",
                thinking_mode="enabled",
                outcome=UsageOutcome.TIMEOUT,
                stable_error_code="request_timeout",
                retryable=True,
            )
        )
        service = UsageQueryService(ledger)

        outcomes = service.breakdown_by_outcome()
        thinking = service.breakdown_by_thinking_mode()
        tasks = service.breakdown_by_task()

    assert [item.key for item in outcomes] == ["success", "timeout"]
    assert [item.key for item in thinking] == ["enabled", "unknown"]
    assert [item.key for item in tasks] == ["task-1", "task-2"]


def test_mixed_category_attempt_does_not_assign_all_tokens_to_first_category(
    tmp_path: Path,
):
    mixed = replace(
        make_event(event_id="mixed", item_count=2),
        category_items=(
            UsageCategoryCount(ScanCategoryId.MOD_LANGUAGE, 1),
            UsageCategoryCount(ScanCategoryId.FTB_QUESTS, 1),
        ),
    )
    with UsageLedger(tmp_path / "usage.sqlite3") as ledger:
        ledger.record_attempt(mixed)
        breakdown = UsageQueryService(ledger).breakdown_by_category()

    assert [item.key for item in breakdown] == [
        "mod_language",
        "ftb_quests",
    ]
    assert all(item.summary.translated_items == 1 for item in breakdown)
    assert all(item.summary.input_tokens is None for item in breakdown)
    assert all(item.summary.estimated_cost is None for item in breakdown)
    assert all(item.summary.reported_cost_total is None for item in breakdown)
    assert all(item.summary.incomplete_usage_count == 1 for item in breakdown)


def test_task_stats_include_cache_reuse_without_api_attempt(tmp_path: Path):
    with UsageLedger(tmp_path / "usage.sqlite3") as ledger:
        ledger.update_task_stats(
            task_id="task-1",
            total_items=5,
            reused_items=3,
            avoided_api_items=4,
            remaining_items=1,
            updated_at="2026-01-01T00:00:00+00:00",
        )
        summary = UsageQueryService(ledger).task_summary("task-1")

    assert summary.api_attempts == 0
    assert summary.reused_items == 3
    assert summary.avoided_api_items == 4
    assert summary.remaining_items == 1


def test_local_validation_count_is_derived_only_from_attempts(tmp_path: Path):
    validation = replace(
        make_event(event_id="validation"),
        outcome=UsageOutcome.LOCAL_VALIDATION_FAILED,
        stable_error_code="local_translation_validation_failed",
        retryable=False,
    )
    with UsageLedger(tmp_path / "usage.sqlite3") as ledger:
        ledger.record_attempt(validation)
        ledger.update_task_stats(
            task_id="task-1",
            total_items=1,
            reused_items=0,
            avoided_api_items=0,
            remaining_items=1,
            updated_at="2026-01-01T00:00:00+00:00",
        )
        summary = UsageQueryService(ledger).task_summary("task-1")

    assert summary.local_validation_failed_items == 1


def test_task_stats_failure_does_not_damage_attempt_transaction(tmp_path: Path):
    path = tmp_path / "usage.sqlite3"
    with UsageLedger(path) as ledger:
        ledger._connection.execute(  # noqa: SLF001 - controlled fault injection.
            """
            CREATE TRIGGER reject_task_stats
            BEFORE INSERT ON task_stats
            BEGIN
                SELECT RAISE(ABORT, 'simulated task stats failure');
            END
            """
        )
        ledger._connection.commit()  # noqa: SLF001
        with pytest.raises(sqlite3.IntegrityError):
            ledger.update_task_stats(
                task_id="task-1",
                total_items=1,
                reused_items=0,
                avoided_api_items=0,
                remaining_items=1,
                updated_at="2026-01-01T00:00:00+00:00",
            )
        assert ledger.record_attempt(make_event(event_id="after-stats-failure"))

    with UsageLedger(path) as ledger:
        assert len(ledger.attempt_rows()) == 1


def test_reported_cost_marks_partial_and_missing_attempts(tmp_path: Path):
    reported = make_event(event_id="reported")
    missing = replace(
        make_event(event_id="missing"),
        provider_reported_cost=None,
        estimated_cost=None,
        currency="",
        pricing_profile_id="",
    )
    with UsageLedger(tmp_path / "usage.sqlite3") as ledger:
        ledger.record_attempt(reported)
        ledger.record_attempt(missing)
        summary = UsageQueryService(ledger).project_summary()

    payload = summary.to_dict()["reported_cost"]
    assert payload == {
        "amount": "0.01",
        "currency": "USD",
        "complete": False,
        "missing_count": 1,
    }


def test_failed_attempts_are_included_in_reported_cost_completeness(
    tmp_path: Path,
):
    failed_without_cost = replace(
        make_event(event_id="failed"),
        outcome=UsageOutcome.TIMEOUT,
        stable_error_code="request_timeout",
        retryable=True,
        provider_reported_cost=None,
        estimated_cost=None,
        currency="",
        pricing_profile_id="",
    )
    with UsageLedger(tmp_path / "usage.sqlite3") as ledger:
        ledger.record_attempt(make_event(event_id="success"))
        ledger.record_attempt(failed_without_cost)
        summary = UsageQueryService(ledger).project_summary()

    assert not summary.reported_cost_complete
    assert summary.missing_reported_cost_count == 1


def test_database_contains_no_secret_or_private_path(tmp_path: Path):
    path = tmp_path / "usage.sqlite3"
    secret = "sk-test-super-secret"
    private = "C:\\Users\\PrivatePerson\\FearNightfall"
    event = make_event(event_id="privacy")
    with UsageLedger(path) as ledger:
        ledger.record_attempt(event)
        payload = json.dumps(
            {
                "summary": UsageQueryService(ledger).project_summary().to_dict()
            },
            ensure_ascii=False,
        )

    database_bytes = path.read_bytes()
    assert secret.encode() not in database_bytes
    assert private.encode() not in database_bytes
    assert secret not in payload
    assert private not in payload


def make_event(
    *,
    event_id: str,
    attempt_number: int = 1,
    outcome: UsageOutcome = UsageOutcome.SUCCESS,
    item_count: int = 1,
) -> ApiAttemptUsage:
    return ApiAttemptUsage(
        event_id=event_id,
        task_id="task-1",
        batch_id="batch-1",
        attempt_number=attempt_number,
        provider="fake",
        model="fake-model",
        endpoint_type="chat_completions",
        thinking_mode="",
        category_items=(
            UsageCategoryCount(ScanCategoryId.MOD_LANGUAGE, item_count),
        ),
        source_types=("jar_lang",),
        item_count=item_count,
        tokens=TokenUsage(
            input_tokens=100,
            output_tokens=50,
            cached_input_tokens=20,
            uncached_input_tokens=80,
            reasoning_tokens=10,
            total_tokens=150,
            reasoning_included_in_output=True,
        ),
        request_started_at="2026-01-01T00:00:00+00:00",
        latency_ms=25,
        outcome=outcome,
        retryable=outcome is not UsageOutcome.SUCCESS,
        stable_error_code=(
            "http_rate_limited"
            if outcome is UsageOutcome.RATE_LIMITED
            else ""
        ),
        provider_request_id="sha256:1234567890abcdef",
        provider_reported_cost=Decimal("0.01"),
        estimated_cost=Decimal("0.02"),
        currency="USD",
        pricing_profile_id="test-profile",
    )
